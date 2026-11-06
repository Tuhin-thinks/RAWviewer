"""ImageAdjustPanelWidget: image adjustment panel, assembled from mixin parts."""
from __future__ import annotations

import theme
from collections import OrderedDict
from typing import Dict

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel, QSlider, QWidget

from raw_adjustments import CHROMA_NR_ON_VALUE, DEFAULT_ADJUSTMENTS, SLIDER_SPECS
from raw_dodge_burn import DEFAULT_STRENGTH as DB_DEFAULT_STRENGTH, STRENGTH_KEY as DB_STRENGTH_KEY
from raw_hsl import HSL_COLOR_NAMES
from raw_tone_curve import TONE_CURVE_BLUE_KEY, TONE_CURVE_GREEN_KEY, TONE_CURVE_RED_KEY, TONE_CURVE_SERIAL_KEY
from .helpers import (
    ADJUST_DOCK_DEFAULT,
    AdjustSlider,
    AdjustValueLabel,
    CollapsibleSection,
    _LutThumbSignals,
    _PARAMETRIC_TONE_KEYS,
    _SHOW_DODGE_BURN_UI,
    _SHOW_HSL_UI,
    _SHOW_TONE_CURVE_UI,
    _SLIDER_TRACK_GRADIENTS,
    _TRANSFORM_SLIDER_KEYS,
    _adjust_combo_stylesheet,
    _edit_settings_clipboard,
    _sanitize_section_order,
)

from .adjust_part1 import AdjustPart1
from .adjust_part2 import AdjustPart2
from .adjust_part3 import AdjustPart3
from .adjust_part4 import AdjustPart4
from .adjust_part5 import AdjustPart5
from .adjust_part6 import AdjustPart6


class ImageAdjustPanelWidget(AdjustPart1, AdjustPart2, AdjustPart3, AdjustPart4, AdjustPart5, AdjustPart6, QWidget):
    """Image adjustment panel widget."""

    def __init__(self, parent=None, histogram_widget=None):
        super().__init__(parent)
        self._sliders: Dict[str, QSlider] = {}
        self._value_labels: Dict[str, QLabel] = {}
        self._block_emit = False
        self._mask_user_hidden = False
        self._as_shot_temperature = float(DEFAULT_ADJUSTMENTS["Temperature"])
        self._recovery_baseline = False
        self._current_hsl_color = HSL_COLOR_NAMES[0]
        self._hsl_cache: Dict[str, float] = {
            k: float(v)
            for k, v in DEFAULT_ADJUSTMENTS.items()
            if k.startswith("HueAdjustment")
            or k.startswith("SaturationAdjustment")
            or k.startswith("LuminanceAdjustment")
        }
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._emit_live_preview)
        self._dock_side = ADJUST_DOCK_DEFAULT
        self._anamorphic_ratio = 1.0
        self._camera_profile_identity = ("", "", None)
        self._lut_thumb_cache: OrderedDict = OrderedDict()
        self._lut_thumb_rows: dict = {}
        self._lut_thumb_token = 0
        self._lut_thumb_display_u8 = None
        self._lut_thumb_path = ""
        self._lut_thumb_source_gen = 0
        self._lut_thumb_signals = _LutThumbSignals()
        self._lut_thumb_signals.finished.connect(self._on_lut_thumbs_ready)
        self._lut_thumb_timer = QTimer(self)
        self._lut_thumb_timer.setSingleShot(True)
        self._lut_thumb_timer.timeout.connect(self._start_lut_thumb_worker)

        self.setMinimumWidth(self._PANEL_W)
        self.setMaximumWidth(560)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Inherit darkroom palette so any child without an explicit color
        # still gets cream ink on Surface — never system light WindowText.
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(*theme.SURFACE_RGB))
        pal.setColor(QPalette.ColorRole.Base, QColor(*theme.SURFACE_RGB))
        pal.setColor(QPalette.ColorRole.AlternateBase, QColor(*theme.RAISED_RGB))
        pal.setColor(QPalette.ColorRole.WindowText, QColor(*theme.INK_RGB))
        pal.setColor(QPalette.ColorRole.Text, QColor(*theme.INK_RGB))
        pal.setColor(QPalette.ColorRole.ButtonText, QColor(*theme.INK_RGB))
        pal.setColor(QPalette.ColorRole.BrightText, QColor(*theme.INK_RGB))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self._build_ui(histogram_widget)
