"""gamma: helpers moved out of fast.py (P2b cycle break)."""

import numpy as np

_GAMMA_LUT8: np.ndarray | None = None


_GAMMA_CURVE16: np.ndarray | None = None


def gamma_curve16() -> np.ndarray:
    """dcraw BT.709 curve, 16-bit in -> 16-bit out (see _gamma_lut8).

    Public: the edit pipeline's display/export encode uses this same curve
    so an edited image at default settings renders identically to browse.
    """
    _gamma_lut8()  # builds both LUTs
    return _GAMMA_CURVE16


def _gamma_lut8() -> np.ndarray:
    """dcraw gamma_curve for gamma=(2.222, 4.5): BT.709 OETF, 16-bit -> 8-bit.

    The toe breakpoint is solved by bisection exactly like dcraw does, and the
    8-bit value is ``curve >> 8`` (truncation) matching dcraw's write_ppm.
    """
    global _GAMMA_LUT8, _GAMMA_CURVE16
    if _GAMMA_LUT8 is not None:
        return _GAMMA_LUT8
    pwr, ts = 1.0 / 2.222, 4.5
    bnd = [0.0, 1.0]
    g2 = 0.0
    for _ in range(48):
        g2 = (bnd[0] + bnd[1]) / 2
        if ((g2 / ts) ** -pwr - 1) / pwr - 1 / g2 > -1:
            bnd[1] = g2
        else:
            bnd[0] = g2
    g3 = g2 / ts
    g4 = g2 * (1 / pwr - 1)
    x = np.arange(65536, dtype=np.float64) / 65535.0
    y = np.where(x < g3, x * ts, (1 + g4) * np.power(np.maximum(x, 1e-12), pwr) - g4)
    curve16 = np.clip(y * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    _GAMMA_CURVE16 = curve16
    _GAMMA_LUT8 = (curve16 >> 8).astype(np.uint8)
    return _GAMMA_LUT8
