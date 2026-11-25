"""shared: helpers moved out of tone_recovery.py (P2b cycle break)."""
import numpy as np


def _luminance(rgb_f: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb_f[:, :, 0] + 0.7152 * rgb_f[:, :, 1] + 0.0722 * rgb_f[:, :, 2]


def _multiply_by_2d_factor(rgb: np.ndarray, factor2d: np.ndarray) -> np.ndarray:
    """rgb(H,W,3) * factor2d(H,W), broadcasting the 2D factor across channels.

    A plain ``rgb * factor2d[..., np.newaxis]`` broadcast, or pre-tiling the
    factor to (H,W,3) via np.repeat, are both slower than a per-channel loop
    with a sliced ``out=`` (measured at 32MP: broadcast 142ms, repeat-tile
    119ms, per-channel-out 91ms) -- numpy's broadcasting iterator doesn't
    take the fast contiguous SIMD path for a trailing size-1 axis, and
    np.repeat pays its own full-size allocation+copy. Bit-identical to the
    broadcast result.
    """
    out = np.empty_like(rgb)
    for c in range(rgb.shape[-1]):
        np.multiply(rgb[:, :, c], factor2d, out=out[:, :, c])
    return out


def circular_brush_falloff(
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    cx: float,
    cy: float,
    radius: float,
) -> np.ndarray:
    """Soft circular brush kernel on ``[y0:y1, x0:x1]`` (peak 1 at center).

    Raised-cosine (Hann) radial profile: full strength at the center, smooth
    gradient to zero at ``radius``. Hard-clips outside ``radius`` so the
    axis-aligned stamp bbox corners stay empty (avoids rectangular live blits).
    """
    r = max(1.0, float(radius))
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    dist = np.sqrt((xx - float(cx)) ** 2 + (yy - float(cy)) ** 2)
    t = np.clip(dist / r, 0.0, 1.0)
    # Hann window: 1 at center, 0 at edge, continuous first derivative.
    falloff = (0.5 * (1.0 + np.cos(np.pi * t))).astype(np.float32)
    falloff[dist > r] = 0.0
    return falloff
