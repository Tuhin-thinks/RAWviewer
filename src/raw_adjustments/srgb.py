"""srgb: helpers moved out of saved_edits.py (P2b cycle break)."""

import numpy as np

_SRGB_DECODE_LUT = None


def _decode_srgb_uint8_to_linear(arr: np.ndarray) -> np.ndarray:
    """Standard sRGB EOTF, uint8 [0,255] -> linear [0,1] float32.

    Gallery/thumbnail buffers for RAW files are the camera's embedded JPEG
    (extract_embedded_jpeg_by_scan / rawpy.extract_thumb) or, absent that,
    LibRaw's own raw.postprocess() output -- both approximately sRGB gamma
    (~2.2 with a linear toe), NOT the app's own dcraw-style BT.709
    linear16->uint8 encode from fast_raw_decode._gamma_lut8. This function
    used to invert THAT LUT (see git history), which assumes an input this
    code path never actually receives: the flatter BT.709 shadow curve,
    inverted, overestimates linear values versus the steeper true sRGB toe,
    so the reconstructed "linear" buffer was too bright even before edits --
    then process_linear_edit_buffer's exposure/tone stage (built for a
    genuinely linear-RAW input, e.g. decode_raw_edit_base) amplified that
    overestimate further, making edited gallery thumbnails visibly brighter
    than the same edit applied in single view (which decodes true
    scene-linear RAW, never through an 8-bit round trip at all). Report:
    "thumbnail preview brightness significantly higher than edited image."
    """
    global _SRGB_DECODE_LUT
    if _SRGB_DECODE_LUT is None:
        c = np.arange(256, dtype=np.float64) / 255.0
        lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
        _SRGB_DECODE_LUT = lin.astype(np.float32)
    return _SRGB_DECODE_LUT[arr]
