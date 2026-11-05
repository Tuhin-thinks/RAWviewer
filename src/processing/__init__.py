"""Processing module package facade.

Provides re-exports of all processing operations organized by domain:
- :mod:`processing.raw`   - RAW image processing operations
- :mod:`processing.image` - Generic image processing operations

from processing.unified import *
from processing.raw import *
from processing.image import *

__all__ = [
    # Facade re-exports
    "unified_image_processor",
    # RAW operations
    "demosaic",
    "color",
    "tone",
    "detail",
    "effects",
    # Image operations
    "resize",
    "transform",
    "effects2",
    "composite",]