"""ZoomController - user zoom state, clamping, persistence and Ctrl+wheel.

Extracted from ``PaletteWindow`` (god-file split, final step): owns the zoom
value (stored in the injected settings dict), the step/cycle rules, the
app-font rescaling every widget needs and the Ctrl+wheel detection.

The window-specific re-render (header buttons, table columns, theme
stylesheets) stays with the palette and arrives as ``on_zoom(zoom)``; the
optional ``on_label(percent)`` keeps a host zoom readout in step. Both
callbacks fire only from ``set_zoom`` / ``step`` / ``apply``.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

import mewgenics_overlay.ui.theme as _theme

ZOOM_MIN = 0.75          # smallest zoom the compact layout still fits at
ZOOM_MAX = 4.0           # largest zoom before it stops being a palette
ZOOM_STEP = 0.25         # Ctrl+wheel / +/- increment
ZOOM_DEFAULT = 1.0       # 100%
ZOOM_CYCLE = (1.0, 1.5, 2.0, 3.0)   # harness/hotkey cycle order
_CYCLE_EPS = 0.001       # float slack when looking for the next cycle step
_MIN_PIXEL_SIZE = 6      # floor for scaled pixel fonts
_MIN_POINT_SIZE = 4.0    # floor for scaled point fonts


class ZoomController(QObject):
    """Zoom level for the overlay, persisted through the host's settings.

    ``settings`` is the live settings dict and ``save_settings`` persists it.
    ``on_zoom(zoom)`` re-renders the window-specific visuals, and
    ``on_label(percent)`` (optional) updates a host zoom readout.
    """

    def __init__(
        self,
        settings: dict,
        save_settings: Callable[[], None],
        on_zoom: Optional[Callable[[float], None]] = None,
        on_label: Optional[Callable[[int], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._save_settings = save_settings
        self._on_zoom = on_zoom
        self._on_label = on_label
        # Captured once at startup: 100% zoom is the baseline the app font
        # scales from, whatever the desktop/DE set it to.
        app = QApplication.instance()
        self._base_font = QFont(app.font()) if app is not None else None

    # ── state ──────────────────────────────────────────────────────────────
    @property
    def zoom(self) -> float:
        return float(self._settings.get("zoom", ZOOM_DEFAULT) or ZOOM_DEFAULT)

    @staticmethod
    def clamp(z: float) -> float:
        """Round to 2 decimals and hold inside the usable zoom range."""
        return round(min(ZOOM_MAX, max(ZOOM_MIN, float(z))), 2)

    # ── user actions (wired to shortcuts / Settings buttons / the tray) ────
    def step(self, delta: float) -> None:
        self.set_zoom(self.zoom + delta)

    def zoom_in(self) -> None:
        self.step(ZOOM_STEP)

    def zoom_out(self) -> None:
        self.step(-ZOOM_STEP)

    def reset(self) -> None:
        self.set_zoom(ZOOM_DEFAULT)

    def cycle(self) -> None:
        """Jump to the next preset above the current zoom (wraps to 100%)."""
        cur = self.zoom
        nxt = next((c for c in ZOOM_CYCLE if c > cur + _CYCLE_EPS),
                   ZOOM_DEFAULT)
        self.set_zoom(nxt)

    def set_zoom(self, z: float) -> None:
        """Persist and apply a new zoom level."""
        z = self.clamp(z)
        self._settings["zoom"] = z
        self._save_settings()
        if self._on_label is not None:
            self._on_label(int(round(z * 100)))
        self.render(z)

    def apply(self) -> None:
        """Render the stored zoom at startup (no settings write)."""
        self.render(self.zoom)

    def handle_wheel(self, event) -> bool:
        """Consume a Ctrl+wheel event and zoom.

        Returns ``True`` when the event was handled (the host must then stop
        handling it) and ``False`` when the host should pass it on.
        """
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.step(ZOOM_STEP if delta > 0 else -ZOOM_STEP)
            event.accept()
            return True
        return False

    # ── rendering ──────────────────────────────────────────────────────────
    def render(self, z: float) -> None:
        """Rescale the app font (all widgets), then let the host restyle.

        The app font drives tables and labels; the host callback handles the
        widgets that cache their own sizes or colours.
        """
        _theme.set_zoom(z)
        app = QApplication.instance()
        base = self._base_font or (app.font() if app is not None else None)
        if app is not None and base is not None:
            nf = QFont(base)
            if base.pixelSize() > 0:
                nf.setPixelSize(max(_MIN_PIXEL_SIZE,
                                    int(round(base.pixelSize() * z))))
            else:
                nf.setPointSizeF(max(_MIN_POINT_SIZE, base.pointSizeF() * z))
            app.setFont(nf)
            for w in app.allWidgets():
                w.setFont(nf)
        if self._on_zoom is not None:
            self._on_zoom(z)
