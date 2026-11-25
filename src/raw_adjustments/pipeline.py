"""Shared edit decode → denoise → PV2012 adjust → display / 16-bit export."""

import logging
import os
import threading

import numpy as np

from core.errors import ExportCancelled, RAWViewerError  # noqa: F401
from raw_adjustments.checks import is_default_adjustments, uses_recovery_tone_map
from raw_adjustments.chroma_denoise import apply_chroma_denoise, apply_luma_denoise, chroma_denoise_enabled
from raw_adjustments.defaults import DEFAULT_ADJUSTMENTS, RECOVERY_BASELINE_KEY
from raw_adjustments.dodge_burn import DEFAULT_STRENGTH as _DB_DEFAULT_STRENGTH
from raw_adjustments.dodge_burn import MASK_KEY as _DB_MASK_KEY
from raw_adjustments.dodge_burn import MASK_OBJ_KEY as _DB_MASK_OBJ_KEY
from raw_adjustments.dodge_burn import STRENGTH_KEY as _DB_STRENGTH_KEY
from raw_adjustments.dodge_burn import resolve_mask_from_adj as _resolve_db_mask
from raw_adjustments.linear import (  # P2b: moved; re-exported for existing importers
    _apply_display_color_adjustments,
    _apply_wb_tint,
    _linear_float_from_buffer,
    _rawrefinery_available,
    _tone_map_for_display,
    ai_backend_name,
    linear_to_display_uint8,
    linear_to_export_uint16_srgb,
    process_linear_edit_buffer,
)
from raw_adjustments.pv2012 import apply_pv2012_tone_rgb
from raw_adjustments.spot_heal import MASK_KEY as _HEAL_MASK_KEY
from raw_adjustments.spot_heal import MASK_OBJ_KEY as _HEAL_MASK_OBJ_KEY
from raw_adjustments.spot_heal import resolve_mask_from_adj as _resolve_heal_mask
from raw_adjustments.tone_curve import TONE_CURVE_SERIAL_KEY
from raw_adjustments.transform import TRANSFORM_KEYS as _TRANSFORM_KEYS

logger = logging.getLogger(__name__)


def _tone_map_reinhard_display(img: np.ndarray) -> np.ndarray:
    """Scene-linear float → display-linear float in [0, 1] (Reinhard shoulder).

    RETIRED as the default editor transform (kept only for the legacy
    recovery-look path): Reinhard maps linear 1.0 to display 0.5, so an
    edited image at DEFAULT settings rendered whites at ~151/255 vs the
    browse render's 225 -- measured as the reported "grey overcast" -- and
    compressed chroma spread by 24%. The default path is now
    _tone_map_clip_display + the dcraw BT.709 encode, matching browse
    exactly in tone reproduction.
    """
    from raw_adjustments.tone_recovery import _luminance

    lum = _luminance(img)
    mapped = lum / (1.0 + np.maximum(lum, 0.0))
    scale = mapped / np.maximum(lum, 1e-6)
    return np.clip(img * scale[..., np.newaxis], 0.0, None)


def run_edit_pipeline(
    rgb_image: np.ndarray,
    adj: dict[str, float],
    *,
    target: str,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
) -> np.ndarray:
    """Single entry for preview and export. ``target`` labels the caller only;
    the stage list and arithmetic are identical for both."""
    if target not in ("preview", "export"):
        raise ValueError(f"unknown pipeline target: {target!r}")
    return process_linear_edit_buffer(
        rgb_image,
        adj,
        preview=False,
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )


def ai_denoise_model_present() -> bool:
    """True when the active AI denoise backend has its model file on disk."""
    if ai_backend_name() == "rawrefinery":
        return _rawrefinery_available()
    from processing.raw_decode.scunet import scunet_model_path

    path = scunet_model_path()
    return bool(path) and os.path.exists(path)


class PreviewStageCache:
    """
    Per-open-file memoization for live Adjust-panel preview recompute.

    Every slider tick used to rerun the entire WB -> exposure -> denoise ->
    PV2012 tone -> tonemap -> sat/vibrance -> HSL -> detail chain from
    scratch, even when only one late-stage control (e.g. Sharpness) changed
    since the previous tick -- see docs/EDIT_PIPELINE.md Performance
    review #2, the root cause of "sluggish/laggy while dragging". This cache
    chains a cheap key comparison per stage: if a stage's own inputs (plus
    whatever stage feeds it) haven't changed since the last call, its cached
    output is reused and every stage after it in the chain is skipped too.

    Only the interactive preview path (_AdjustPreviewWorker) uses this. Export
    always calls process_linear_edit_buffer / _apply_display_stage directly
    with no cache, so a bug here cannot corrupt a baked export.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.base_ref = None
        self.stage_keys: dict[str, tuple] = {}
        self.stage_out: dict[str, np.ndarray] = {}


# TRANSFORM_KEYS is imported from raw_transform so Distortion / AnamorphicRatio
# cannot drift out of the preview cache (a hand-maintained copy missed them
# on development and the controls looked dead).

_PRE_TONE_KEYS = (
    "Temperature",
    "Tint",
    "Exposure2012",
    "ColorNoiseReduction",
    "LuminanceNoiseReduction",
    "DenoiseMethod",
    _DB_MASK_KEY,
    _DB_MASK_OBJ_KEY,
    _DB_STRENGTH_KEY,
    _HEAL_MASK_KEY,
    _HEAL_MASK_OBJ_KEY,
    # Geometry runs at the head of the pipeline (before WB), so any transform
    # change invalidates pre_tone and everything chained to it.
    *_TRANSFORM_KEYS,
)

# Live-drag (preview_lite) may denoise before warp so Transform-slider ticks
# only re-run apply_geometry — see process_linear_edit_buffer_staged.
_PRE_TONE_KEYS_NO_GEO = tuple(k for k in _PRE_TONE_KEYS if k not in _TRANSFORM_KEYS)

# Every key uses_recovery_tone_map() and apply_pv2012_tone_rgb() read, so the
# "tone" stage key alone is enough to detect any change that would flip the
# recovery-vs-PV2012 branch or alter the PV2012 tone result.
_TONE_KEYS = (
    "Contrast2012",
    "Highlights2012",
    "Shadows2012",
    "Whites2012",
    "Blacks2012",
    "ParametricShadows",
    "ParametricDarks",
    "ParametricLights",
    "ParametricHighlights",
    RECOVERY_BASELINE_KEY,
    TONE_CURVE_SERIAL_KEY,
)


def _hsl_color_keys() -> tuple[str, ...]:
    from raw_adjustments.hsl import HSL_COLOR_NAMES

    return tuple(
        f"{prefix}Adjustment{color}" for color in HSL_COLOR_NAMES for prefix in ("Hue", "Saturation", "Luminance")
    )


_COLOR_KEYS = (
    "Saturation",
    "Vibrance",
    "PostCropVignetteAmount",
    "PostCropVignetteMidpoint",
    "Dehaze",
) + _hsl_color_keys()

_DETAIL_KEYS = ("Sharpness", "Clarity2012", "Defringe")


def _stage_key(merged: dict, keys: tuple) -> tuple:
    parts = []
    for k in keys:
        v = merged.get(k, 0.0)
        if v is None:
            # Missing/blank XMP leftovers: string-ish keys stay "", numeric stay 0.
            parts.append(
                ""
                if k in ("CreativeLUTName", "CreativeLUTWorkingSpace")
                or str(k).endswith("Mask")
                or "Mask" in str(k)
                or "Curve" in str(k)
                else 0.0
            )
            continue
        if hasattr(v, "version") and hasattr(v, "data"):
            # Live DodgeBurnMask or HealMask object: O(1) version fingerprint!
            h, w = v.data.shape[:2]
            parts.append(f"mem:{int(h)}x{int(w)}:v{int(v.version)}")
            continue
        if isinstance(v, str):
            if v.startswith("mem:"):
                parts.append(v)
            elif (
                len(v) > 256 or str(k).endswith("Mask") or "Mask" in str(k) or k.endswith("Curve") or "Curve" in str(k)
            ):
                import hashlib

                parts.append(hashlib.sha1(v.encode("utf-8", errors="replace")).hexdigest())
            else:
                parts.append(v)
            continue
        # Harden against blank leftovers — a TypeError here used to be swallowed
        # by _AdjustPreviewWorker and leave the live preview stuck on the last frame.
        try:
            parts.append(round(float(v), 6))
        except (TypeError, ValueError):
            parts.append(0.0)
    return tuple(parts)


def process_linear_edit_buffer_staged(
    rgb_image: np.ndarray,
    adj: dict[str, float],
    cache: PreviewStageCache,
    *,
    preview: bool = True,
    chroma_denoise: bool | None = None,
    preview_lite: bool = False,
    settle_fast: bool = False,
) -> np.ndarray:
    """Cached equivalent of process_linear_edit_buffer for the preview path.

    ``preview_lite`` selects the cheaper PV2012 path (live-drag only). Live
    and settle use separate PreviewStageCache instances in main.py, so lite
    tone outputs cannot be reused after a full-quality settle.
    ``settle_fast`` keeps y0 guided filter but skips chroma-damp on settle.
    """
    if rgb_image is None:
        return rgb_image
    merged = dict(DEFAULT_ADJUSTMENTS)
    merged.update(adj or {})

    with cache.lock:
        if cache.base_ref is not rgb_image:
            cache.reset()
            cache.base_ref = rgb_image

        if is_default_adjustments(merged) and not uses_recovery_tone_map(merged):
            # Fast path bypasses pre_tone/tone entirely, so it must still
            # stamp a distinct "tone" key -- otherwise a later call landing
            # back on this fast path (or the transition into it) would leave
            # the stale key from the last *real* computation in place, and
            # _apply_display_stage_staged's tonemap stage (keyed only off
            # this metadata, not off buffer identity) would wrongly reuse a
            # tonemap output computed from different pixel data.
            processed = _linear_float_from_buffer(rgb_image)
            cache.stage_keys["tone"] = ("__default_fastpath__",)
            cache.stage_out["tone"] = processed
            return processed

        pre_key = _stage_key(merged, _PRE_TONE_KEYS)
        if cache.stage_keys.get("pre_tone") != pre_key or "pre_tone" not in cache.stage_out:
            img = _linear_float_from_buffer(rgb_image)
            from raw_adjustments.transform import apply_geometry

            # preview_lite: apply WB/denoise on the uncropped frame once, then
            # warp. Transform-slider ticks then only invalidate the cheap
            # geometry stage instead of re-running denoise every angle step.
            # Settle / export keep geometry-first order (correct for D&B).
            if preview_lite:
                pre_geo_key = _stage_key(merged, _PRE_TONE_KEYS_NO_GEO)
                if cache.stage_keys.get("pre_geom") != pre_geo_key or "pre_geom" not in cache.stage_out:
                    pre = img
                    pre = _apply_wb_tint(pre, merged)
                    exp_val = float(merged.get("Exposure2012", 0.0))
                    if abs(exp_val) > 1e-4:
                        pre = pre * (2.0**exp_val)
                    mask = _resolve_db_mask(merged)
                    if mask is not None:
                        from raw_adjustments.dodge_burn import apply_dodge_burn

                        stops = float(merged.get(_DB_STRENGTH_KEY, _DB_DEFAULT_STRENGTH))
                        pre = apply_dodge_burn(pre, mask, stops)
                    heal_mask = _resolve_heal_mask(merged)
                    if heal_mask is not None:
                        from raw_adjustments.spot_heal import apply_spot_heal

                        pre = apply_spot_heal(pre, heal_mask)
                    # Skip denoise on lite transform path — largest cost after
                    # warp; settle_fast / full path still applies NR.
                    cache.stage_out["pre_geom"] = pre
                    cache.stage_keys["pre_geom"] = pre_geo_key
                geo_key = (pre_geo_key, _stage_key(merged, _TRANSFORM_KEYS))
                if cache.stage_keys.get("geom") != geo_key or "geom" not in cache.stage_out:
                    cache.stage_out["geom"] = apply_geometry(cache.stage_out["pre_geom"], merged, preview=True)
                    cache.stage_keys["geom"] = geo_key
                img = cache.stage_out["geom"]
            else:
                img = apply_geometry(img, merged, preview=False)
                img = _apply_wb_tint(img, merged)

                exp_val = float(merged.get("Exposure2012", 0.0))
                if abs(exp_val) > 1e-4:
                    img *= 2.0**exp_val

                mask = _resolve_db_mask(merged)
                if mask is not None:
                    from raw_adjustments.dodge_burn import apply_dodge_burn

                    stops = float(merged.get(_DB_STRENGTH_KEY, _DB_DEFAULT_STRENGTH))
                    img = apply_dodge_burn(img, mask, stops)

                heal_mask = _resolve_heal_mask(merged)
                if heal_mask is not None:
                    from raw_adjustments.spot_heal import apply_spot_heal

                    img = apply_spot_heal(img, heal_mask)

                do_denoise = chroma_denoise if chroma_denoise is not None else chroma_denoise_enabled()
                nr_amount = float(merged.get("ColorNoiseReduction", 0.0))
                method = int(float(merged.get("DenoiseMethod", 0.0)))
                if nr_amount > 1e-4:
                    img = apply_chroma_denoise(
                        img, strength=min(1.5, nr_amount / 100.0 * 1.25), method=method, preview=preview
                    )
                elif do_denoise and not preview:
                    img = apply_chroma_denoise(img, method=method, preview=False)

                luma_nr_amount = float(merged.get("LuminanceNoiseReduction", 0.0))
                if luma_nr_amount > 1e-4:
                    img = apply_luma_denoise(img, strength=luma_nr_amount / 100.0, method=method, preview=preview)

            cache.stage_out["pre_tone"] = img
            cache.stage_keys["pre_tone"] = pre_key
        pre_tone_out = cache.stage_out["pre_tone"]

        # Chained to pre_key: the tone stage's *input* is pre_tone_out, so a
        # change to a pre-tone-only key (e.g. Exposure2012) must invalidate
        # the tone stage too, even though no _TONE_KEYS value moved.
        # preview_lite is part of the key so flipping lite↔full never reuses
        # the other path's tone buffer (belt-and-suspenders vs dual caches).
        tone_key = (
            pre_key,
            _stage_key(merged, _TONE_KEYS),
            bool(preview_lite),
            bool(settle_fast),
        )
        if cache.stage_keys.get("tone") != tone_key or "tone" not in cache.stage_out:
            if uses_recovery_tone_map(merged):
                toned = pre_tone_out
            else:
                toned = apply_pv2012_tone_rgb(
                    pre_tone_out,
                    merged,
                    preview_lite=bool(preview_lite),
                    settle_fast=bool(settle_fast),
                )
            cache.stage_out["tone"] = np.clip(toned, 0.0, None)
            cache.stage_keys["tone"] = tone_key
        return cache.stage_out["tone"]


def _apply_display_stage_staged(img: np.ndarray, merged: dict[str, float], cache: PreviewStageCache) -> np.ndarray:
    """Cached equivalent of _apply_display_stage for the preview path."""
    from raw_adjustments.detail_enhance import apply_detail_enhancements
    from raw_adjustments.hsl import apply_hsl_adjustments

    with cache.lock:
        tone_key = cache.stage_keys.get("tone")

        tonemap_key = (tone_key,)
        if cache.stage_keys.get("tonemap") != tonemap_key or "tonemap" not in cache.stage_out:
            cache.stage_out["tonemap"] = _tone_map_for_display(img, merged)
            cache.stage_keys["tonemap"] = tonemap_key
        display = cache.stage_out["tonemap"]

        color_key = (cache.stage_keys.get("tonemap"), _stage_key(merged, _COLOR_KEYS))
        if cache.stage_keys.get("color") != color_key or "color" not in cache.stage_out:
            out = _apply_display_color_adjustments(display, merged, preview=True)
            out = apply_hsl_adjustments(out, merged)
            cache.stage_out["color"] = out
            cache.stage_keys["color"] = color_key
        colored = cache.stage_out["color"]

        detail_key = (cache.stage_keys.get("color"), _stage_key(merged, _DETAIL_KEYS))
        if cache.stage_keys.get("detail") != detail_key or "detail" not in cache.stage_out:
            cache.stage_out["detail"] = apply_detail_enhancements(colored, merged)
            cache.stage_keys["detail"] = detail_key
        return cache.stage_out["detail"]


def render_adjust_preview_uint8(
    rgb_image: np.ndarray,
    adj: dict[str, float],
    cache: PreviewStageCache,
    *,
    preview_lite: bool = False,
    settle_fast: bool = False,
) -> np.ndarray:
    """
    Staged/cached equivalent of process_linear_edit_buffer(preview=True) +
    linear_to_display_uint8, reusing intermediate buffers across calls on the
    same base image when only later-stage adjustment keys changed since the
    previous call. Used exclusively by the interactive Adjust-panel preview
    path (_AdjustPreviewWorker in main.py).

    ``preview_lite=True`` for the downsampled live-drag base only.
    ``settle_fast=True`` for Adjust settle (keeps y0 guide, skips chroma damp);
    export keeps the full PV2012 path.

    Encode must match ``linear_to_display_uint8`` (dcraw BT.709 / ``_gamma_lut8``),
    not IEC sRGB OETF — otherwise staged preview diverges from the uncached
    path and smoke tests (and on-screen parity with settle/export) break.
    """
    from processing.raw_decode.gamma import _gamma_lut8
    from raw_adjustments.lut import apply_pipeline_lut_encoded, apply_pipeline_lut_linear
    from raw_adjustments.tone_curve import apply_channel_curves_encoded

    merged = dict(DEFAULT_ADJUSTMENTS)
    merged.update(adj or {})
    processed = process_linear_edit_buffer_staged(
        rgb_image,
        merged,
        cache,
        preview=True,
        preview_lite=preview_lite,
        settle_fast=settle_fast,
    )
    display = _apply_display_stage_staged(processed, merged, cache)
    display = apply_pipeline_lut_linear(display, merged)
    # Copy before encode: display may be a live cache buffer; returning a view
    # into stage_out would let the next worker tick mutate pixels already
    # queued for the UI thread's QPixmap conversion.
    idx = np.clip(display * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    encoded = np.ascontiguousarray(_gamma_lut8()[idx])
    encoded = apply_pipeline_lut_encoded(encoded, merged, 255.0)
    return apply_channel_curves_encoded(encoded, merged, 255.0)


def _ensure_parent_dir(output_path: str) -> None:
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _chroma_denoise_for_export(adj: dict[str, float]) -> bool:
    return float((adj or {}).get("ColorNoiseReduction", 0.0)) > 0 or chroma_denoise_enabled()


def _process_for_export(
    rgb_linear: np.ndarray,
    adj: dict[str, float],
    *,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
) -> np.ndarray:
    return process_linear_edit_buffer(
        rgb_linear,
        adj,
        preview=False,
        chroma_denoise=_chroma_denoise_for_export(adj),
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )


def _xmp_extratags(embed_xmp_path: str | None) -> list:
    if not embed_xmp_path or not os.path.isfile(embed_xmp_path):
        return []
    with open(embed_xmp_path, "rb") as f:
        xmp_bytes = f.read()
    return [(700, "B", len(xmp_bytes), xmp_bytes, False)]


def _write_16bit_rgb_tiff(
    output_path: str,
    rgb_uint16: np.ndarray,
    *,
    embed_xmp_path: str | None = None,
) -> None:
    """
    Write a genuine 16-bit-per-channel, 3-sample RGB TIFF via ``tifffile``.

    Pillow's ``"RGB"`` mode is defined as 8-bit-per-channel -- there is no
    Pillow mode for 16-bit 3-channel data, so ``Image.fromarray(arr,
    mode="RGB")`` with a uint16 array has no valid type-lookup entry. On
    Pillow >=~10 (this project pins ">=10.0.0" with no upper bound) that
    raises outright; workarounds via ``Image.frombuffer`` with a "RGB;16*"
    raw mode "succeed" but silently discard the low byte, since the
    underlying storage is 8-bit regardless. Both are unacceptable for a
    "16-bit TIFF" export, so this path bypasses Pillow's Image model
    entirely for 16-bit RGB. ``compression="deflate"`` (not the previous
    LZW) needs no extra codec package; ``lzw`` requires ``imagecodecs``,
    which isn't a project dependency.
    """
    import tifffile

    extratags = _xmp_extratags(embed_xmp_path)
    _ensure_parent_dir(output_path)
    tifffile.imwrite(
        output_path,
        rgb_uint16,
        photometric="rgb",
        compression="deflate",
        extratags=extratags or None,
    )


def export_adjusted_tiff16(
    rgb_linear: np.ndarray,
    adj: dict[str, float],
    output_path: str,
    *,
    embed_xmp_path: str | None = None,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
) -> None:
    """Bake adjustments to 16-bit sRGB TIFF; optionally embed XMP packet."""
    processed = _process_for_export(
        rgb_linear,
        adj,
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )
    out = linear_to_export_uint16_srgb(processed, adj)
    _write_16bit_rgb_tiff(output_path, out, embed_xmp_path=embed_xmp_path)


def export_adjusted_jpeg(
    rgb_linear: np.ndarray,
    adj: dict[str, float],
    output_path: str,
    *,
    quality: int = 92,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
) -> None:
    """Bake adjustments to 8-bit JPEG."""
    processed = _process_for_export(
        rgb_linear,
        adj,
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )
    out = linear_to_display_uint8(processed, adj)
    from PIL import Image

    im = Image.fromarray(out, mode="RGB")
    _ensure_parent_dir(output_path)
    im.save(
        output_path,
        format="JPEG",
        quality=max(1, min(100, int(quality))),
        subsampling=0,
        # Deliberately no optimize=True: that flag combined with
        # subsampling=0 triggers a libjpeg "Suspension not allowed here"
        # encoder crash on some Pillow/libjpeg builds -- reproduced with a
        # plain synthetic array, unrelated to any RAWViewer-specific data.
        # subsampling=0 (no chroma subsampling) matters for color fidelity;
        # optimize is a pure file-size optimization with no visible quality
        # difference, not worth the crash risk.
    )


def export_adjusted_webp(
    rgb_linear: np.ndarray,
    adj: dict[str, float],
    output_path: str,
    *,
    quality: int = 95,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
) -> None:
    """Bake adjustments to 8-bit WebP.

    Default quality 95 + method 6: at q88/method 4 lossy WebP flattens smooth
    denoised gradients into visible 16x16 macroblock grid banding.
    """
    processed = _process_for_export(
        rgb_linear,
        adj,
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )
    out = linear_to_display_uint8(processed, adj)
    from PIL import Image

    im = Image.fromarray(out, mode="RGB")
    _ensure_parent_dir(output_path)
    im.save(
        output_path,
        format="WEBP",
        quality=max(1, min(100, int(quality))),
        method=6,
    )


EXPORT_FORMAT_TIFF16 = "tiff16"
EXPORT_FORMAT_JPEG = "jpeg"
EXPORT_FORMAT_WEBP = "webp"


def export_adjusted_image(
    export_format: str,
    *,
    output_path: str,
    rgb_linear: np.ndarray | None,
    adj: dict[str, float],
    embed_xmp_path: str | None = None,
    cancel_check=None,
    progress_cb=None,
    use_ai_denoise: bool = False,
) -> None:
    """Dispatch baked export (TIFF16 / JPEG / WebP)."""
    fmt = (export_format or EXPORT_FORMAT_TIFF16).strip().lower()
    extra = {
        "use_ai_denoise": use_ai_denoise,
        "cancel_check": cancel_check,
        "progress_cb": progress_cb,
    }
    if rgb_linear is None:
        raise RuntimeError("Full-resolution RAW decode failed")
    if cancel_check is not None and cancel_check():
        raise ExportCancelled()
    if fmt == EXPORT_FORMAT_JPEG:
        export_adjusted_jpeg(rgb_linear, adj, output_path, **extra)
    elif fmt == EXPORT_FORMAT_WEBP:
        export_adjusted_webp(rgb_linear, adj, output_path, **extra)
    else:
        export_adjusted_tiff16(
            rgb_linear,
            adj,
            output_path,
            embed_xmp_path=embed_xmp_path,
            **extra,
        )
