"""Linear-light edit pipeline: buffer processing and display conversion.

Moved out of pipeline.py (P2b) so apply, lut and saved_edits can import it without
importing the full pipeline, which breaks the raw_adjustments import cycle.
"""

import logging
import os

import numpy as np

from raw_adjustments.checks import is_default_adjustments, uses_recovery_tone_map
from raw_adjustments.chroma_denoise import apply_chroma_denoise, apply_luma_denoise, chroma_denoise_enabled
from raw_adjustments.defaults import DEFAULT_ADJUSTMENTS
from raw_adjustments.dodge_burn import DEFAULT_STRENGTH as _DB_DEFAULT_STRENGTH
from raw_adjustments.dodge_burn import STRENGTH_KEY as _DB_STRENGTH_KEY
from raw_adjustments.dodge_burn import resolve_mask_from_adj as _resolve_db_mask
from raw_adjustments.pv2012 import apply_pv2012_tone_rgb
from raw_adjustments.spot_heal import resolve_mask_from_adj as _resolve_heal_mask

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


_scunet_availability_logged = False


def _linear_float_from_buffer(rgb_image: np.ndarray) -> np.ndarray:
    if rgb_image.dtype == np.uint16:
        return rgb_image.astype(np.float32) / 65535.0
    if rgb_image.dtype == np.float32:
        return np.clip(rgb_image.astype(np.float32), 0.0, None)
    return rgb_image.astype(np.float32) / 255.0


def _kelvin_to_rgb(k: float) -> tuple[float, float, float]:
    from raw_adjustments.checks import _kelvin_to_rgb as kelvin

    return kelvin(k)


def _apply_wb_tint(img: np.ndarray, merged: dict[str, float]) -> np.ndarray:
    from raw_adjustments.checks import wb_reference_temperature

    ref_temp = wb_reference_temperature(merged)
    temp_val = float(merged.get("Temperature", ref_temp))
    tint_val = float(merged.get("Tint", 0.0))
    if temp_val > 1000.0 and abs(temp_val - ref_temp) > 1.0:
        r_tgt, g_tgt, b_tgt = _kelvin_to_rgb(temp_val)
        r_ref, g_ref, b_ref = _kelvin_to_rgb(ref_temp)
        if g_ref > 1e-5 and g_tgt > 1e-5:
            img[:, :, 0] *= (r_tgt / r_ref) / (g_tgt / g_ref)
            img[:, :, 2] *= (b_tgt / b_ref) / (g_tgt / g_ref)
    elif abs(temp_val) > 1e-4 and temp_val <= 1000.0:
        scale = temp_val / 100.0
        img[:, :, 0] *= 1.0 + scale * 0.1
        img[:, :, 2] *= 1.0 - scale * 0.1
    if abs(tint_val) > 1e-4:
        img[:, :, 1] *= 1.0 - (tint_val / 150.0) * 0.1
    return img


def _apply_saturation_vibrance(img: np.ndarray, merged: dict[str, float]) -> np.ndarray:
    """
    Saturation / vibrance via HSV S-channel scale in gamma-encoded (perceptual) space.

    Scaling chroma (img - luma) directly on scene-linear RGB made the result wildly
    hue-dependent: hues with large linear-light channel spread (green, yellow) blew
    past the gamut boundary and clipped to a flat, fully-saturated block, while hues
    with small linear spread (skin tones, blue sky) barely moved — the "colors look
    off / no pop" symptom. Round-tripping through the sRGB OETF (nearest-index LUT,
    not np.power — see docs/EDIT_PIPELINE.md) and scaling HSV S there matches
    how a "Saturation" slider is perceived, and S is bounded to [0, 1] by construction
    so it can't overshoot the gamut the way additive chroma scaling did.
    """
    import cv2

    from raw_adjustments.tone_recovery import _decode_srgb_float01, _encode_srgb_float01

    sat_val = float(merged.get("Saturation", 0.0))
    vib_val = float(merged.get("Vibrance", 0.0))
    if abs(sat_val) < 1e-4 and abs(vib_val) < 1e-4:
        return img

    rgb_lin = np.clip(img.astype(np.float32), 0.0, 1.0)
    rgb_enc = _encode_srgb_float01(rgb_lin)
    bgr = np.ascontiguousarray(rgb_enc[..., ::-1])
    h, s, v = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV))

    scale = np.ones_like(s)
    if abs(sat_val) > 1e-4:
        scale = scale + sat_val / 100.0
    if abs(vib_val) > 1e-4:
        scale = scale + (vib_val / 100.0) * (1.0 - s)
    s = np.clip(s * scale, 0.0, 1.0)

    bgr_out = cv2.cvtColor(cv2.merge([h, s, v]), cv2.COLOR_HSV2BGR)
    rgb_enc_out = np.clip(bgr_out[..., ::-1], 0.0, 1.0)
    return _decode_srgb_float01(rgb_enc_out)


def _tone_map_clip_display(img: np.ndarray) -> np.ndarray:
    """Scene-linear float → display-linear [0, 1] by hard clip (browse parity).

    The browse pipeline saturates highlights at the camera clip point and
    applies the dcraw BT.709 curve; clipping here reproduces that identically
    for the editor's default rendering.
    """
    return np.clip(img.astype(np.float32, copy=False), 0.0, 1.0)


def _tone_map_recovery_display(img: np.ndarray) -> np.ndarray:
    """Recovery preview tone path (matches P-key recovery, including edge cap)."""
    from raw_adjustments.tone_recovery import (
        RECOVERY_MAX_EDGE,
        _cap_max_edge_linear,
        apply_local_shadow_highlight_recovery_display,
        linear_tone_map_to_display,
    )

    h, w = img.shape[:2]
    rgb_f = img.astype(np.float32)
    if max(h, w) <= RECOVERY_MAX_EDGE:
        display = linear_tone_map_to_display(rgb_f)
        return apply_local_shadow_highlight_recovery_display(display)

    small = _cap_max_edge_linear(rgb_f, RECOVERY_MAX_EDGE)
    polished = apply_local_shadow_highlight_recovery_display(linear_tone_map_to_display(small))
    import cv2

    up = cv2.resize(polished, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.clip(up, 0.0, 1.0).astype(np.float32)


def _tone_map_for_display(img: np.ndarray, merged: dict[str, float]) -> np.ndarray:
    if uses_recovery_tone_map(merged):
        # Legacy sessions only: the recovery-look button was removed from
        # the Adjust panel (2026-07); nothing sets the baseline flag anymore.
        return _tone_map_recovery_display(img)
    return _tone_map_clip_display(img)


def _apply_display_stage(
    img: np.ndarray,
    merged: dict[str, float],
    *,
    y_range: tuple[int, int] | None = None,
    total_h: int | None = None,
) -> np.ndarray:
    """Tone map → sat/vib → HSL → detail (display-linear float [0, 1])."""
    from raw_adjustments.detail_enhance import apply_detail_enhancements
    from raw_adjustments.hsl import apply_hsl_adjustments

    display = _tone_map_for_display(img, merged)
    if merged:
        display = _apply_display_color_adjustments(display, merged, preview=False, y_range=y_range, total_h=total_h)
        display = apply_hsl_adjustments(display, merged)
    display = apply_detail_enhancements(display, merged)
    return display


def _linear_pipeline_worker_count(height: int) -> int:
    # Shared tuning point with raw_adjustments' banded sRGB pipeline.
    from raw_adjustments.banding import banded_worker_count

    return banded_worker_count(height)


def process_linear_edit_buffer(
    rgb_image: np.ndarray,
    adj: dict[str, float],
    *,
    preview: bool = False,
    chroma_denoise: bool | None = None,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
) -> np.ndarray:
    """
    Scene-linear uint16/float → adjusted scene-linear float32.

    Order: WB → exposure → chroma/luma denoise (bilateral) → PV2012 tone → saturation.
    """
    if rgb_image is None:
        return rgb_image
    merged = dict(DEFAULT_ADJUSTMENTS)
    merged.update(adj or {})
    img = _linear_float_from_buffer(rgb_image)

    # AI Denoise is export-only and independent of slider defaults: do not
    # skip the pipeline when the user explicitly chose it from the menu.
    if is_default_adjustments(merged) and not uses_recovery_tone_map(merged) and not use_ai_denoise:
        return img

    # Geometry first: every later stage (dodge/burn coordinates included)
    # works in the transformed frame. No-op unless transform keys are set.
    from raw_adjustments.transform import apply_geometry

    img = apply_geometry(img, merged)

    n_workers = _linear_pipeline_worker_count(img.shape[0])
    if n_workers > 1:
        return _process_linear_edit_buffer_banded(
            img,
            merged,
            n_workers,
            preview=preview,
            chroma_denoise=chroma_denoise,
            use_ai_denoise=use_ai_denoise,
            cancel_check=cancel_check,
            progress_cb=progress_cb,
        )

    return _process_linear_edit_tail(
        img,
        merged,
        preview=preview,
        chroma_denoise=chroma_denoise,
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )


def _process_linear_edit_pre_ai(
    img: np.ndarray,
    merged: dict[str, float],
    *,
    preview: bool,
    chroma_denoise: bool | None,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
    row_offset: int = 0,
    full_height: int | None = None,
) -> np.ndarray:
    """WB -> exposure -> dodge/burn -> spot heal -> legacy chroma NR (non-AI only). Row-local, safe to band."""
    img = _apply_wb_tint(img, merged)

    exp_val = float(merged.get("Exposure2012", 0.0))
    if abs(exp_val) > 1e-4:
        img = img * (2.0**exp_val)

    # Dodge & burn: local per-pixel exposure, applied right after the
    # global exposure gain and before denoise/tone -- local brightness
    # changes should see the same noise/tone response as global ones
    # (matches how a real exposure difference at capture time would look).
    mask = _resolve_db_mask(merged)
    if mask is not None:
        from raw_adjustments.dodge_burn import apply_dodge_burn

        stops = float(merged.get(_DB_STRENGTH_KEY, _DB_DEFAULT_STRENGTH))
        img = apply_dodge_burn(img, mask, stops)

    # Spot heal (cv2.inpaint): after local exposure so the fill samples the
    # already-exposed neighborhood; before denoise/tone.
    heal_mask = _resolve_heal_mask(merged)
    if heal_mask is not None:
        from raw_adjustments.spot_heal import apply_spot_heal

        img = apply_spot_heal(img, heal_mask, row_offset=row_offset, full_height=full_height)

    if not use_ai_denoise:
        img = _apply_legacy_chroma_denoise(img, merged, chroma_denoise=chroma_denoise, preview=preview)
    return img


def _apply_legacy_chroma_denoise(
    img: np.ndarray,
    merged: dict[str, float],
    *,
    chroma_denoise: bool | None,
    preview: bool,
) -> np.ndarray:
    do_denoise = chroma_denoise if chroma_denoise is not None else chroma_denoise_enabled()
    nr_amount = float(merged.get("ColorNoiseReduction", 0.0))
    method = int(float(merged.get("DenoiseMethod", 0.0)))
    if nr_amount > 1e-4:
        return apply_chroma_denoise(img, strength=min(1.5, nr_amount / 100.0 * 1.25), method=method, preview=preview)
    if do_denoise and not preview:
        return apply_chroma_denoise(img, method=method, preview=False)
    return img


def ai_backend_name() -> str:
    """Active AI denoise backend: "rawrefinery" (default) or "scunet" (dormant fallback)."""
    name = os.environ.get("RAWVIEWER_AI_BACKEND", "rawrefinery").strip().lower()
    return "scunet" if name == "scunet" else "rawrefinery"


def _rawrefinery_available() -> bool:
    from raw_adjustments.refinery_denoise import model_available

    return model_available()


def _apply_ai_denoise_stage(
    img: np.ndarray,
    merged: dict[str, float],
    *,
    use_ai_denoise: bool,
    preview: bool,
    cancel_check=None,
    progress_cb=None,
) -> np.ndarray:
    """AI barrier: runs on the whole image, never on bands."""
    if use_ai_denoise and ai_backend_name() == "rawrefinery" and _rawrefinery_available():
        from raw_adjustments.refinery_denoise import denoise as rawrefinery_denoise

        ai_amount = max(0.0, min(1.0, float(merged.get("AIDenoiseAmount", 100.0)) / 100.0))
        if ai_amount <= 1e-4:
            return img
        return rawrefinery_denoise(
            img,
            strength=ai_amount,
            cancel_check=cancel_check,
            progress_callback=progress_cb,
        )

    use_scunet = False
    model_path = ""
    if use_ai_denoise:
        from processing.raw_decode.scunet import SCUNetONNX, scunet_model_path

        model_path = scunet_model_path()
        scunet_enabled_env = (
            os.environ.get("RAWVIEWER_EXPORT_SCUNET_ONNX", os.environ.get("RAWVIEWER_EXPORT_RESTORMER_ONNX", "1"))
            == "1"
        )
        scunet_model_present = os.path.exists(model_path) if model_path else False
        # AI denoise is export-only and opt-in: the Adjust panel's Chroma NR
        # dropdown (Bilateral/Guided/Off) always applies as selected, live in
        # preview and by default at export -- use_ai_denoise (set by the Export
        # menu's "AI Denoise" toggle) is what additionally switches the export
        # pass over to SCUNet/NAFNet instead of the legacy method.
        use_scunet = scunet_enabled_env and scunet_model_present

        global _scunet_availability_logged
        if not _scunet_availability_logged:
            _scunet_availability_logged = True
            if not scunet_enabled_env:
                logger.info("[DENOISE] AI denoise (SCUNet ONNX) disabled via RAWVIEWER_EXPORT_SCUNET_ONNX=0")
            elif not scunet_model_present:
                logger.warning(
                    "[DENOISE] AI denoise model not found at %s -- falling back to legacy chroma/luma noise reduction.",
                    model_path,
                )
            else:
                logger.info("[DENOISE] AI denoise model found at %s", model_path)

    if use_scunet:
        # The AI Denoise export submenu is explicit user intent: run the model
        # unconditionally. (Previously gated on nr_amount/do_denoise, which
        # silently exported a non-denoised file when the NR sliders were 0.)
        # Full resolution for preview and export alike (no half-res default).
        downscale = 1
        detail_gain = float(merged.get("DenoiseDetail", 50.0)) / 100.0
        ai_amount = float(merged.get("AIDenoiseAmount", 100.0)) / 100.0
        ai_amount = max(0.0, min(1.0, ai_amount))
        logger.info(
            "[DENOISE] Using AI denoise (model=%s, %s, detail=%.0f%%, amount=%.0f%%) for this export",
            os.path.basename(model_path),
            "full-res" if downscale == 1 else "half-res+detail-restore",
            detail_gain * 100.0,
            ai_amount * 100.0,
        )
        scunet = SCUNetONNX(model_path)

        def _denoise_progress(frac: float) -> None:
            if progress_cb is not None:
                progress_cb(frac)

        if ai_amount > 1e-4:
            img = scunet.process(
                img,
                cancel_check=cancel_check,
                progress_callback=_denoise_progress,
                downscale=downscale,
                detail_gain=detail_gain,
                strength=ai_amount,
            )
        else:
            logger.info("[DENOISE] AI denoise amount is 0%% -- exporting without AI denoise")
    return img


def _process_linear_edit_post_ai(
    img: np.ndarray,
    merged: dict[str, float],
    *,
    preview: bool,
    row_offset: int = 0,
    full_height: int | None = None,
) -> np.ndarray:
    """Luma NR -> PV2012 tone on a (possibly banded) buffer. Row-local except luma filter pad."""
    method = int(float(merged.get("DenoiseMethod", 0.0)))
    luma_nr_amount = float(merged.get("LuminanceNoiseReduction", 0.0))
    if luma_nr_amount > 1e-4:
        img = apply_luma_denoise(img, strength=luma_nr_amount / 100.0, method=method, preview=preview)

    if not uses_recovery_tone_map(merged):
        img = apply_pv2012_tone_rgb(img, merged)
    return np.clip(img, 0.0, None)


def _process_linear_edit_buffer_banded(
    img: np.ndarray,
    merged: dict[str, float],
    n_workers: int,
    *,
    preview: bool = False,
    chroma_denoise: bool | None = None,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
) -> np.ndarray:
    """Row-band parallel pipeline: banded pre-AI, whole-image AI barrier, banded post-AI."""
    from raw_adjustments.banding import band_ranges, banded_executor

    # 16px overlap pad handles bilateral / guided filter radii (up to r=12) and spot heal
    full_h = int(img.shape[0])
    bands = band_ranges(full_h, n_workers, pad_px=16)

    def _run_banded(stage, buf, **kwargs):
        def _process_band(band):
            y0, y1, pad_top, pad_bot = band
            # Copy, not view: _apply_wb_tint multiplies in place, and bands'
            # 16px pad regions overlap -- on a shared view, overlapping rows get
            # WB applied twice (and raced on by concurrent bands).
            src = buf[y0 - pad_top : y1 + pad_bot].copy()
            out = stage(
                src,
                merged,
                row_offset=y0 - pad_top,
                full_height=full_h,
                **kwargs,
            )
            return out[pad_top : pad_top + (y1 - y0)]

        return np.concatenate(list(banded_executor().map(_process_band, bands)), axis=0)

    img = _run_banded(
        _process_linear_edit_pre_ai,
        img,
        preview=preview,
        chroma_denoise=chroma_denoise,
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
    )
    if use_ai_denoise:
        # AI is not row-independent: one session over the whole image, never per band.
        img = _apply_ai_denoise_stage(
            img,
            merged,
            use_ai_denoise=True,
            preview=preview,
            cancel_check=cancel_check,
            progress_cb=progress_cb,
        )
    return _run_banded(_process_linear_edit_post_ai, img, preview=preview)


def _process_linear_edit_tail(
    img: np.ndarray,
    merged: dict[str, float],
    *,
    preview: bool,
    chroma_denoise: bool | None,
    use_ai_denoise: bool = False,
    cancel_check=None,
    progress_cb=None,
    row_offset: int = 0,
    full_height: int | None = None,
) -> np.ndarray:
    """Single-pass pipeline: pre-AI -> AI barrier -> post-AI."""
    img = _process_linear_edit_pre_ai(
        img,
        merged,
        preview=preview,
        chroma_denoise=chroma_denoise,
        use_ai_denoise=use_ai_denoise,
        cancel_check=cancel_check,
        progress_cb=progress_cb,
        row_offset=row_offset,
        full_height=full_height,
    )
    if use_ai_denoise:
        img = _apply_ai_denoise_stage(
            img,
            merged,
            use_ai_denoise=True,
            preview=preview,
            cancel_check=cancel_check,
            progress_cb=progress_cb,
        )
    return _process_linear_edit_post_ai(
        img,
        merged,
        preview=preview,
        row_offset=row_offset,
        full_height=full_height,
    )


def _apply_display_color_adjustments(
    display_linear: np.ndarray,
    merged: dict[str, float],
    *,
    preview: bool = False,
    y_range: tuple[int, int] | None = None,
    total_h: int | None = None,
) -> np.ndarray:
    """Saturation / vibrance / vignette / dehaze in display-linear space.

    Creative LUT is applied later via ``apply_pipeline_lut_*`` (Rec.709 after
    gamma encode by default; Linear keeps the pre-encode path).
    """
    from raw_adjustments.effects import (
        VIGNETTE_MIDPOINT_DEFAULT,
        VIGNETTE_MIDPOINT_KEY,
        apply_dehaze,
        apply_vignette,
    )

    out = _apply_saturation_vibrance(display_linear, merged)
    dehaze = float(merged.get("Dehaze", 0.0) or 0.0)
    if abs(dehaze) > 1e-3:
        out = apply_dehaze(out, dehaze, preview=preview)
    vignette = float(merged.get("PostCropVignetteAmount", 0.0) or 0.0)
    if abs(vignette) > 1e-3:
        midpoint = float(merged.get(VIGNETTE_MIDPOINT_KEY, VIGNETTE_MIDPOINT_DEFAULT) or VIGNETTE_MIDPOINT_DEFAULT)
        out = apply_vignette(out, vignette, midpoint=midpoint, y_range=y_range, total_h=total_h)
    return out


_dither_tile_cache: np.ndarray | None = None


def _dither_tile() -> np.ndarray:
    """512x512 triangular-PDF dither tile in [-1, 1), built once, deterministic."""
    global _dither_tile_cache
    if _dither_tile_cache is None:
        rng = np.random.default_rng(0xD17E)
        _dither_tile_cache = rng.random((512, 512), dtype=np.float32) - rng.random((512, 512), dtype=np.float32)
    return _dither_tile_cache


def linear_to_display_uint8(img: np.ndarray, adj: dict[str, float] | None = None) -> np.ndarray:
    # dcraw BT.709 encode, NOT the IEC sRGB OETF: browse renders encode with
    # this exact curve, and the two differ most in the toe (12.92x vs 4.5x
    # linear slope) -- the sRGB encode lifted the deepest shadows ~3x over
    # the browse render, surfacing sensor noise ("grainy" report). One curve
    # everywhere = editor default is pixel-comparable with browse.
    from processing.raw_decode.gamma import gamma_curve16
    from raw_adjustments.lut import apply_pipeline_lut_encoded, apply_pipeline_lut_linear
    from raw_adjustments.tone_curve import apply_channel_curves_encoded

    merged = dict(adj or {})
    n_workers = _linear_pipeline_worker_count(img.shape[0])
    if not uses_recovery_tone_map(merged) and n_workers > 1:
        display = _apply_display_stage_banded(img, merged, n_workers)
    else:
        display = _apply_display_stage(img, merged)
    display = apply_pipeline_lut_linear(display, merged)
    idx = np.clip(display * 65535.0 + 0.5, 0, 65535).astype(np.uint16)

    # TPDF dither (±1 LSB of the 8-bit output) applied in ENCODED space,
    # immediately before the 8-bit quantization it exists to smooth.
    # Denoised smooth gradients otherwise posterize into visible banding on
    # 8-bit export -- the sensor noise that used to dither them for free is
    # gone. Deterministic tiled pattern (no per-call RNG state, no
    # full-frame random buffer) so exports stay byte-reproducible.
    #
    # This used to add +/-1/255 to the LINEAR buffer instead. The BT.709
    # toe has a 4.5x slope, so near black that perturbation came out as
    # +/-4-5 output codes rather than +/-1: deep shadows were visibly
    # noisy, a black frame quantized to 1 instead of 0, and a ramp lost
    # monotonicity. Dithering after the encode makes "1 LSB" mean one LSB
    # at every brightness, which is the whole point of dithering.
    tile = _dither_tile()
    th, tw = display.shape[0], display.shape[1]
    d = tile[np.arange(th) % tile.shape[0]][:, np.arange(tw) % tile.shape[1]]

    # 256 units of the 16-bit encoded curve == 1 LSB of the 8-bit output.
    enc16 = gamma_curve16()[idx].astype(np.float32) + (d * 256.0)[..., None]
    # >> 8 (truncation) matches dcraw's write_ppm and _gamma_lut8 exactly,
    # so a zero-dither pixel encodes bit-identically to the old path.
    encoded = (np.clip(enc16, 0.0, 65535.0).astype(np.uint16) >> 8).astype(np.uint8)

    encoded = apply_pipeline_lut_encoded(encoded, merged, 255.0)
    return apply_channel_curves_encoded(encoded, merged, 255.0)


def _apply_display_stage_banded(img: np.ndarray, merged: dict[str, float], n_workers: int) -> np.ndarray:
    """Row-band parallel version of _apply_display_stage (tone map, sat/vib,
    HSL, detail-enhance). Only called for the clip tone-map path (the
    recovery path downsamples the whole image via cv2.resize and isn't
    row-independent). Reuses raw_adjustments._BAND_PAD_PX -- same blur radii,
    same padding math as _apply_adjustments_to_srgb_banded.
    """
    from raw_adjustments.banding import _BAND_PAD_PX, band_ranges, banded_executor
    from raw_adjustments.effects import DEHAZE_KEY

    if abs(float(merged.get(DEHAZE_KEY, 0.0) or 0.0)) > 1e-3:
        return _apply_display_stage(img, merged)

    total_h = img.shape[0]
    bands = band_ranges(total_h, n_workers, pad_px=_BAND_PAD_PX)

    def _process_band(band):
        y0, y1, pad_top, pad_bot = band
        # Copy, not view: guard against any in-place stage (same seam bug
        # class as the linear banded path -- see _process_linear_edit_buffer_banded).
        src = img[y0 - pad_top : y1 + pad_bot].copy()
        out = _apply_display_stage(src, merged, y_range=(y0 - pad_top, y1 + pad_bot), total_h=total_h)
        return out[pad_top : pad_top + (y1 - y0)]

    import cv2

    prev_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        results = list(banded_executor().map(_process_band, bands))
    finally:
        cv2.setNumThreads(prev_threads)
    return np.concatenate(results, axis=0)


def linear_to_export_uint16_srgb(img: np.ndarray, adj: dict[str, float] | None = None) -> np.ndarray:
    """Display-referred 16-bit export, same dcraw BT.709 curve as display.

    (Name kept for call-site stability; the output is the app's standard
    BT.709-encoded rendering, tagged/treated as sRGB exactly like every
    LibRaw/dcraw-derived pipeline does.)
    """
    from processing.raw_decode.gamma import gamma_curve16
    from raw_adjustments.lut import apply_pipeline_lut_encoded, apply_pipeline_lut_linear
    from raw_adjustments.tone_curve import apply_channel_curves_encoded

    merged = dict(adj or {})
    display = _apply_display_stage(img, merged)
    display = apply_pipeline_lut_linear(display, merged)
    idx = np.clip(display * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    encoded = apply_pipeline_lut_encoded(gamma_curve16()[idx], merged, 65535.0)
    return apply_channel_curves_encoded(encoded, merged, 65535.0)
