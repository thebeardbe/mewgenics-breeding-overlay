"""WindowController - frameless-window and OS behaviour for the palette.

Extracted from ``PaletteWindow`` (god-file split, step 6): owns everything the
window does as an OS object rather than as a content view - framing flags,
geometry restore/save, always-on-top pinning (native on Windows), the
click-through toggle, summon/hide, and the focus-loss auto click-through.

It knows nothing about breeding data: the live settings dict and the "persist
settings" callable arrive from the host, and the host keeps its own Qt event
overrides (``showEvent`` / ``hideEvent`` / ``changeEvent``) as thin stubs that
call ``on_show`` / ``on_hide`` / ``on_change`` here.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from mewgenics_overlay.ui.chrome import TopBar

log = logging.getLogger("mewgenics_overlay.ui")

PIN_DEFAULT = True               # 📌 button starts engaged
CLICK_THROUGH_DEFAULT = False    # window starts interactive


class WindowController(QObject):
    """Window-level behaviour for the overlay palette.

    ``window`` is the palette widget and ``chrome`` its header bar (whose
    pin/click-through buttons mirror this state). ``settings`` is the live
    settings dict and ``save_settings`` persists it after geometry changes.
    """

    def __init__(
        self,
        window: QWidget,
        chrome: TopBar,
        settings: dict,
        save_settings: Callable[[], None],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent if parent is not None else window)
        self._window = window
        self._chrome = chrome
        self._settings = settings
        self._save_settings = save_settings
        self._pinned = PIN_DEFAULT
        self._click_through = CLICK_THROUGH_DEFAULT
        self._dialog_open = False

    # ── state (mutated through the methods below) ──────────────────────────
    @property
    def pinned(self) -> bool:
        return self._pinned

    @property
    def click_through(self) -> bool:
        return self._click_through

    @property
    def dialog_open(self) -> bool:
        return self._dialog_open

    @dialog_open.setter
    def dialog_open(self, on: bool) -> None:
        self._dialog_open = bool(on)

    # ── framing / geometry ─────────────────────────────────────────────────
    def configure_frame(self) -> None:
        """Frameless palette; the Qt topmost flag only where it is safe.

        Pinning is done natively on Windows (SetWindowPos) and via compositor
        rules on Hyprland; only generic X11/Wayland keep the Qt flag, which
        re-creates the native window when toggled (Windows hides it -> the
        "can't find it anymore" bug).
        """
        flags = Qt.WindowType.FramelessWindowHint
        if sys.platform != "win32" and not os.environ.get(
                "HYPRLAND_INSTANCE_SIGNATURE"):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self._window.setWindowFlags(flags)

    def restore_geometry(self) -> None:
        """Restore the last window rect, clamped to a visible screen."""
        rect = self._settings.get("window_rect")
        if not (isinstance(rect, list) and len(rect) == 4):
            return
        r = QRect(*rect)
        screens = QGuiApplication.screens()
        if any(r.intersects(s.availableGeometry()) for s in screens):
            self._window.setGeometry(r)

    def save_geometry(self) -> None:
        g = self._window.geometry()
        self._settings["window_rect"] = [g.x(), g.y(), g.width(), g.height()]
        self._save_settings()

    # ── pin / always-on-top ────────────────────────────────────────────────
    def toggle_pin(self, checked: bool) -> None:
        """Pin toggle. Never re-creates the native window on Windows."""
        self._pinned = bool(checked)
        self._chrome.set_pinned(self._pinned)
        if sys.platform == "win32":
            self._set_topmost_win32(self._pinned)
        elif not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            # generic X11/Wayland: Qt flag fallback (may flash once)
            self._window.setWindowFlag(
                Qt.WindowType.WindowStaysOnTopHint, self._pinned)
            self._window.show()
        # Hyprland: stacking is controlled by compositor rules - visual only.

    def _set_topmost_win32(self, on: bool) -> None:
        """Set/unset always-on-top without touching window flags (no HWND
        re-creation -> the overlay can't get 'lost')."""
        try:
            import ctypes
            hwnd = int(self._window.winId())
            HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST if on else HWND_NOTOPMOST,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            # Never crash the overlay for a cosmetic pin; keep a log line so
            # the failure is visible instead of silent.
            log.warning("native topmost toggle failed (topmost=%s)", on)

    # ── click-through ──────────────────────────────────────────────────────
    def on_click_through_clicked(self, checked: bool) -> None:
        self.set_click_through(checked)

    def set_click_through(self, on: bool) -> None:
        """When ON, mouse events pass through to the game underneath."""
        self._click_through = bool(on)
        self._window.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            self._click_through)
        self._chrome.set_click_through(self._click_through)

    # ── summon / hide ──────────────────────────────────────────────────────
    def engage(self) -> None:
        """Show the palette and make it interactive (hotkey/tray summon)."""
        self.set_click_through(False)
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        self._window.setFocus()

    def toggle_activate(self) -> None:
        """Hotkey/tray cycle: hidden -> engage; passive -> engage; active -> hide."""
        if not self._window.isVisible():
            self.engage()
        elif self._click_through:
            self.engage()
        else:
            self._window.hide()

    # ── Qt event hooks (called from the host's event overrides) ────────────
    def on_show(self) -> None:
        if sys.platform == "win32":
            self._set_topmost_win32(self._pinned)

    def on_hide(self) -> None:
        self.save_geometry()

    def on_change(self, event) -> None:
        """The moment the window loses focus (user clicks the game), stop
        intercepting mouse input: switch to click-through automatically so the
        game always receives clicks in this area. Summon it again with
        Ctrl+Shift+B / tray to interact. Skipped while a modal dialog is open."""
        if (event.type() == QEvent.Type.WindowDeactivate
                and not self._dialog_open
                and self._window.isVisible()
                and not self._click_through):
            self.set_click_through(True)
