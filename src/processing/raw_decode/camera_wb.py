"""camera_wb: helpers moved out of fast.py (P2b cycle break)."""
import os

_WB_CORRECTION_CACHE: dict[str, list[float] | None] = {}


def get_corrected_camera_wb(file_path: str) -> list[float] | None:
    """Session-cached corrected camera WB multipliers for ``file_path``.

    Non-None only when this file's as-shot WB failed the embedded-JPEG sanity
    check during :func:`unpack_raw` (e.g. bodies newer than the bundled
    LibRaw's makernote parser, like the EOS R6 Mark III). Callers that decode
    via rawpy directly (edit base, EDR) should pass it as ``user_wb`` with
    ``use_camera_wb=False`` so every pipeline shows the same corrected color.
    """
    key = os.path.normcase(os.path.abspath(file_path))
    return _WB_CORRECTION_CACHE.get(key)


def wb_sanity_enabled() -> bool:
    """As-shot WB sanity check vs embedded JPEG (RAWVIEWER_WB_SANITY=0 disables)."""
    return str(os.environ.get("RAWVIEWER_WB_SANITY", "1")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
