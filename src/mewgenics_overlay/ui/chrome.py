"""TopBar - the overlay's header row (grip, title, status, action buttons).

Extracted from ``PaletteWindow`` (god-file split, step 3): owns the frameless
drag grip, the title and status labels, and the three icon buttons (pin,
click-through, hide).

It knows nothing about the window behind it: the initial checked states and
the button actions arrive as plain values and callables, and the host drives
the labels and the per-zoom styling through ``set_title`` / ``set_status`` /
``set_pinned`` / ``set_click_through`` / ``restyle`` / ``scale_buttons``.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from mewgenics_overlay.ui import theme as _theme

_HEAD_SPACING = 12              # gap between grip / title / status / buttons
_HEAD_MARGINS = (4, 2, 6, 2)    # header padding inside the palette frame
_PIN_TEXT = "📌"
_CT_TEXT = "🧿"
_CLOSE_TEXT = "✕"
_BTN_BASE_W = 46                # icon-button width at 100% zoom
_BTN_BASE_H = 26                # icon-button height at 100% zoom
_BTN_MIN_W = 38                 # narrowest the buttons may shrink to
_BTN_MIN_H = 22                 # shortest the buttons may shrink to


class _DragLabel(QLabel):
    """Header grip that starts an OS window move on left-drag."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            wh = self.window().windowHandle()
            if wh is not None and wh.startSystemMove():
                self._dragging = False
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)


class TopBar(QWidget):
    """The palette header row: drag grip, title, status and icon buttons.

    ``pinned`` / ``click_through`` seed the checkable buttons; ``on_pin`` and
    ``on_click_through`` receive the new checked state, ``on_hide`` takes no
    arguments.
    """

    def __init__(
        self,
        pinned: bool = True,
        click_through: bool = False,
        on_pin: Optional[Callable[[bool], None]] = None,
        on_click_through: Optional[Callable[[bool], None]] = None,
        on_hide: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_pin = on_pin
        self._on_click_through = on_click_through
        self._on_hide = on_hide

        head = QHBoxLayout(self)
        head.setSpacing(_HEAD_SPACING)
        head.setContentsMargins(*_HEAD_MARGINS)
        grip = _DragLabel("⠿")
        self._grip = grip
        grip.setStyleSheet(f"color:{_theme.C_GRIP}; font-size:13px;")
        grip.setToolTip("Drag to move the overlay")
        self._title = _DragLabel("🐈 Breeding Overlay")
        self._title.setStyleSheet("font-weight:700; font-size:14px;")
        self._title.setCursor(Qt.CursorShape.OpenHandCursor)
        self._status = QLabel("")
        self._status.setObjectName("muted")
        self._status.setStyleSheet(
            f"color:{_theme.C_STATUS}; font-size:11px;")
        head.addWidget(grip)
        head.addWidget(self._title)
        head.addWidget(self._status, 1)

        self._btn_pin = self._icon_button(
            _PIN_TEXT,
            "Keep above the game (native pin on Windows, "
            "Hyprland rules on Linux)",
            checkable=True, checked=bool(pinned))
        self._btn_pin.clicked.connect(self._emit_pin)
        self._btn_ct = self._icon_button(
            _CT_TEXT,
            "Click-through: let mouse clicks reach Mewgenics. "
            "Ctrl+Shift+B / tray to interact.",
            checkable=True, checked=bool(click_through))
        self._btn_ct.clicked.connect(self._emit_click_through)
        self._btn_close = self._icon_button(
            _CLOSE_TEXT,
            "Hide (Ctrl+Shift+B / tray) - quits when no tray is available")
        self._btn_close.clicked.connect(self._emit_hide)
        head.addWidget(self._btn_pin)
        head.addWidget(self._btn_ct)
        head.addWidget(self._btn_close)

    # ── construction helper ───────────────────────────────────────────────
    @staticmethod
    def _icon_button(text: str, tip: str, checkable: bool = False,
                     checked: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(checkable)
        btn.setChecked(checked)
        btn.setObjectName("iconbtn")
        btn.setFixedSize(_BTN_BASE_W, _BTN_BASE_H)
        btn.setToolTip(tip)
        return btn

    # ── callbacks to the host ─────────────────────────────────────────────
    def _emit_pin(self, checked: bool) -> None:
        if self._on_pin is not None:
            self._on_pin(checked)

    def _emit_click_through(self, checked: bool) -> None:
        if self._on_click_through is not None:
            self._on_click_through(checked)

    def _emit_hide(self) -> None:
        if self._on_hide is not None:
            self._on_hide()

    # ── labels (read-only, for hosts that still reach for them) ───────────
    @property
    def title_label(self) -> QLabel:
        return self._title

    @property
    def status_label(self) -> QLabel:
        return self._status

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    # ── button state ──────────────────────────────────────────────────────
    def set_pinned(self, on: bool) -> None:
        """Mirror the pin state without re-firing the click callback."""
        self._set_checked(self._btn_pin, on)

    def set_click_through(self, on: bool) -> None:
        """Mirror the click-through state without re-firing the callback."""
        self._set_checked(self._btn_ct, on)

    @staticmethod
    def _set_checked(btn: QPushButton, on: bool) -> None:
        btn.blockSignals(True)
        btn.setChecked(bool(on))
        btn.blockSignals(False)

    # ── zoom ──────────────────────────────────────────────────────────────
    def restyle(self, zoom: float) -> None:
        """Header grip/title/status font sizes follow the zoom level."""
        _theme.set_zoom(zoom)
        self._grip.setStyleSheet(
            f"color:{_theme.C_GRIP}; font-size:{_theme.zoom_px(13)}px;")
        self._title.setStyleSheet(
            f"font-weight:700; font-size:{_theme.zoom_px(14)}px; "
            f"color:{_theme.C_TEXT};")
        self._status.setStyleSheet(
            f"color:{_theme.C_STATUS}; font-size:{_theme.zoom_px(11)}px;")

    def scale_buttons(self, zoom: float) -> None:
        """Buttons share one height and one glyph width at every zoom."""
        h = max(_BTN_MIN_H, int(_BTN_BASE_H * zoom))
        w = max(_BTN_MIN_W, int(_BTN_BASE_W * zoom))
        for btn in (self._btn_pin, self._btn_ct, self._btn_close):
            btn.setFixedSize(w, h)
