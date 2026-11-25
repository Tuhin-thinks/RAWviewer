"""sRGB decode, already-adjusted tracking, and apply_saved_edits_for_display."""

import os
import threading
import weakref

import numpy as np

from raw_adjustments.srgb import (  # moved (P2b); re-exported
    _decode_srgb_uint8_to_linear,
)

from .checks import is_default_adjustments
from .xmp_read import edited_previews_enabled, load_adjustments_for_file, resolve_xmp_path

# id(arr) -> weakref to that exact array.
#
# This was a bare set of id()s, which was wrong in BOTH directions. numpy
# hands the same address back for the next same-shaped allocation, so a fresh
# preview at a recycled address was read as "already adjusted" and skipped its
# XMP edits. And the overflow guard called set.pop(), which removes an
# ARBITRARY element -- often a live, still-in-flight buffer -- so that one got
# adjusted a second time.
#
# The weakref settles identity (a recycled address cannot match, because the
# referent is dead or different) and the finalizer keeps the map from growing,
# which removes the need to evict live entries at all.
_already_adjusted_ids: dict[int, weakref.ref] = {}
_already_adjusted_lock = threading.Lock()


def mark_array_as_already_adjusted(arr) -> None:
    if arr is None:
        return
    key = id(arr)

    def _drop(_ref, _key=key) -> None:
        with _already_adjusted_lock:
            _already_adjusted_ids.pop(_key, None)

    try:
        ref = weakref.ref(arr, _drop)
    except TypeError:
        # Not weak-referenceable: better to re-apply than to record a mark
        # that can never be validated.
        return
    with _already_adjusted_lock:
        _already_adjusted_ids[key] = ref


def is_array_already_adjusted(arr) -> bool:
    """Whether THIS array object was marked -- not merely its address."""
    if arr is None:
        return False
    with _already_adjusted_lock:
        ref = _already_adjusted_ids.get(id(arr))
    return ref is not None and ref() is arr


def apply_saved_edits_for_display(file_path: str, arr):
    """Apply saved XMP edits to a display-bound uint8 RGB ndarray."""
    if is_array_already_adjusted(arr):
        return arr
    """Apply saved XMP edits to a display-bound uint8 RGB ndarray.

    Returns the input unchanged when previews-with-edits is disabled, the
    file is not RAW, the file has no (non-default) saved adjustments, or the
    buffer is not an ndarray (QImage branches skip). Never raises. Callers
    must NOT persist the result into pixel caches -- caches hold the
    unadjusted base.

    RAW-only by contract: sidecars are resolved by BASENAME (Lightroom
    convention), so a JPEG sharing its basename with an edited RAW resolves
    to the RAW's sidecar -- without this gate the JPEG's gallery tile showed
    the RAW's edits applied to it ("also flagged as edited" report). Edits
    can only ever be created for RAW files (the Adjust panel requires a
    demosaiced RAW base), so non-RAW is always pass-through.

    Renders through the REAL scene-linear pipeline (sRGB decode ->
    process_linear_edit_buffer -> linear_to_display_uint8), not the legacy
    gamma-space approximation: the gamma path's tone math diverges visibly
    from the Adjust panel's render (reported as the gallery/fit preview
    being "way off" from the edited image). Working from an already
    tone-curved camera JPEG is still an approximation of the true RAW edit
    base, but the tone/color processing applied on top is now the same code
    the editor runs. The uint8->linear decode uses the standard sRGB EOTF,
    matching the actual gamma of the embedded-JPEG/LibRaw-postprocess
    thumbnail buffers this function receives (see
    _decode_srgb_uint8_to_linear's docstring for why the BT.709 LUT this
    used previously was the wrong inverse and made edited thumbnails read
    brighter than the true edited image).
    """
    try:
        if arr is None or not hasattr(arr, "shape") or not edited_previews_enabled():
            return arr
        from common_image_loader import is_raw_file

        if not is_raw_file(file_path):
            return arr
        # Sidecar existence FIRST (two isfile checks, ~0.05ms). Going straight
        # to load_adjustments_for_file ran read_as_shot_temperature -- a full
        # EXIF parse of the RAW file -- for EVERY delivered gallery tile,
        # edited or not, before the is_default early-out could save it. On a
        # cold session that extra per-tile file-open competed with thumbnail
        # extraction on the same disk: "gallery scrolling slower than before".
        xmp_path = resolve_xmp_path(file_path)
        if not (xmp_path and os.path.isfile(xmp_path)):
            return arr
        adj = load_adjustments_for_file(file_path)
        if is_default_adjustments(adj):
            return arr
        from raw_adjustments.linear import linear_to_display_uint8, process_linear_edit_buffer

        lin = _decode_srgb_uint8_to_linear(np.ascontiguousarray(arr))
        out = process_linear_edit_buffer(lin, adj, preview=True)
        return linear_to_display_uint8(out, adj)
    except Exception:
        return arr
