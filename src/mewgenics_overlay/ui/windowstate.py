"""WindowController - frameless-window and OS behaviour for the palette.

Extracted from ``PaletteWindow`` (god-file split, step 6): owns everything the
window does as an OS object rather than as a content view - framing flags,
geometry restore/save, always-on-top (native on Windows), the click-through
toggle, summon/hide, and the focus-loss auto click-through.

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

from mewgenics_overlay.ui import raisewindow
from mewgenics_overlay.ui.chrome import TopBar

log = logging.getLogger("mewgenics_overlay.ui")

CLICK_THROUGH_DEFAULT = False    # window starts interactive


class WindowController(QObject):
    """Window-level behaviour for the overlay palette.

    ``window`` is the palette widget and ``chrome`` its header bar (whose
    click-through button mirrors this state). ``settings`` is the live
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
        self._click_through = CLICK_THROUGH_DEFAULT
        self._dialog_open = False

    # ── state (mutated through the methods below) ──────────────────────────
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

        The overlay is kept above the game natively on Windows (SetWindowPos,
        re-applied in :meth:`on_show`) and via compositor rules on Hyprland;
        only generic X11/Wayland keep the Qt flag, which re-creates the native
        window when toggled (Windows hides it -> the "can't find it anymore"
        bug).
        """
        flags = Qt.WindowType.FramelessWindowHint
        if sys.platform != "win32" and not os.environ.get(
                "HYPRLAND_INSTANCE_SIGNATURE"):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self._window.setWindowFlags(flags)

    def restore_geometry(self) -> None:
        """Restore the last window rect, clamped to a visible screen.

        ``config.load`` already normalises a stored rect, but the value can
        also arrive injected (tests, an old settings dict), so it is coerced
        and validated here too: anything unusable is ignored with a log line
        instead of raising.
        """
        rect = self._settings.get("window_rect")
        if rect is None:
            return                      # no remembered geometry - not an error
        nums = None
        if isinstance(rect, (list, tuple)) and len(rect) == 4:
            try:
                nums = [int(v) for v in rect]
            except (TypeError, ValueError):
                nums = None
        if nums is None or nums[2] <= 0 or nums[3] <= 0:
            log.warning("ignoring unusable window_rect %r", rect)
            return
        r = QRect(*nums)
        screens = QGuiApplication.screens()
        if any(r.intersects(s.availableGeometry()) for s in screens):
            self._window.setGeometry(r)

    def save_geometry(self) -> None:
        g = self._window.geometry()
        self._settings["window_rect"] = [g.x(), g.y(), g.width(), g.height()]
        self._save_settings()

    # ── always-on-top ──────────────────────────────────────────────────────
    def _set_topmost_win32(self) -> None:
        """Keep the overlay above other windows without touching window flags
        (no HWND re-creation -> the overlay can't get 'lost')."""
        try:
            import ctypes
            hwnd = int(self._window.winId())
            HWND_TOPMOST = -1
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            # Never crash the overlay for a cosmetic always-on-top; keep a log
            # line so the failure is visible instead of silent.
            log.warning("native always-on-top setup failed")

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
        # Qt only *asks*; on Hyprland a compositor rule can still keep the
        # palette behind the game, so nudge the compositor directly. No-op
        # (and quiet) on every other session.
        raisewindow.focus_window()

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
            self._set_topmost_win32()

    def on_hide(self) -> None:
        self.save_geometry()

    def on_change(self, event) -> None:
        """The moment the window loses focus (user clicks the game), stop
        intercepting mouse input: switch to click-through automatically so the
        game always receives clicks in this area. Summon it again with the
        configured global hotkey / tray to interact. Skipped while a modal
        dialog is open."""
        if (event.type() == QEvent.Type.WindowDeactivate
                and not self._dialog_open
                and self._window.isVisible()
                and not self._click_through):
            self.set_click_through(True)
