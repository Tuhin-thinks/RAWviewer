"""Background RAW / pixmap processing.

Package split from original ``rawviewer_app/processing.py``.
Re-exports the full public surface for backward compatibility.
"""
from ._raw_part2 import RAWProcessorPart2
from ._pixmap_converters import PixmapConverter, RawEdrPixmapConverter


class RAWProcessor(RAWProcessorPart2):
    """Composite RAW image processor (assembled from part 1 + part 2 via MRO)."""