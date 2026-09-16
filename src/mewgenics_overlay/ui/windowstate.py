"""WindowController - frameless-window and OS behaviour for the palette.

Extracted from ``PaletteWindow`` (god-file split, step 6): owns everything the
window does as an OS object rather than as a content view - framing flags,
geometry restore/save, the user's keep-on-top choice (native on Windows), the
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

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from mewgenics_overlay.ui import config as cfg
from mewgenics_overlay.ui import raisewindow
from mewgenics_overlay.ui.chrome import TopBar

log = logging.getLogger("mewgenics_overlay.ui")

CLICK_THROUGH_DEFAULT = False    # window starts interactive

# Windows only: the settle burst after a raise. A single SetWindowPos can lose
# the race because the game re-asserts its own topmost the moment it loses the
# foreground. A fixed, small number of re-asserts over a couple of hundred
# milliseconds wins that race and then stops; an open-ended timer would fight
# the game forever and pin the overlay over windows the user brought forward.
WIN32_TOPMOST_REASSERTS = 3
WIN32_TOPMOST_REASSERT_INTERVAL_MS = 80    # 3 x 80 ms = ~240 ms total


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
        self._keep_on_top = bool(
            settings.get("keep_on_top", cfg.DEFAULTS["keep_on_top"]))
        self._dialog_open = False
        # True only while engage() maps the window on Windows, so on_show does
        # not assert topmost before the window is foreground (the flicker).
        self._engaging = False
        # Lazy bounded re-assert timer (Windows only) plus how many beats are
        # still due; created on the first Windows engage with keep-on-top on.
        self._topmost_timer: Optional[QTimer] = None
        self._topmost_reasserts_left = 0

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

    @property
    def keep_on_top(self) -> bool:
        """The user's stored keep-on-top choice (tray "Keep on top")."""
        return self._keep_on_top

    # ── framing / geometry ─────────────────────────────────────────────────
    def configure_frame(self) -> None:
        """Frameless palette; the Qt topmost flag only where it is safe.

        The overlay is kept above the game natively on Windows (SetWindowPos,
        re-applied in :meth:`on_show`) and via compositor rules on Hyprland;
        only generic X11/Wayland keep the Qt flag, which re-creates the native
        window when toggled (Windows hides it -> the "can't find it anymore"
        bug). The stored "Keep on top" choice seeds the flag here.
        """
        flags = Qt.WindowType.FramelessWindowHint
        if self._uses_qt_topmost() and self._keep_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self._window.setWindowFlags(flags)

    @staticmethod
    def _hyprland() -> bool:
        """True when a Hyprland session owns window stacking via rules."""
        return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))

    @classmethod
    def _uses_qt_topmost(cls) -> bool:
        """True where the Qt topmost flag is the safe mechanism."""
        return sys.platform != "win32" and not cls._hyprland()

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
    def set_keep_on_top(self, on: bool) -> None:
        """Apply the tray's "Keep on top" choice immediately and persist it."""
        self._keep_on_top = bool(on)
        self._settings["keep_on_top"] = self._keep_on_top
        self._save_settings()
        if not self._keep_on_top:
            # A raise may have left a settle burst pending; stop it so the
            # choice the user just made is not overridden a beat later.
            self._stop_topmost_timer()
        self._apply_keep_on_top()

    def _apply_keep_on_top(self) -> None:
        """Apply the stored choice: native on Windows, the Qt hint on generic
        X11/Wayland, and a log note on Hyprland (which owns stacking)."""
        if sys.platform == "win32":
            self._set_topmost_win32(self._keep_on_top)
        elif self._hyprland():
            self._log_hyprland_stacking()
        else:
            self._set_topmost_qt(self._keep_on_top)

    def _set_topmost_win32(self, on: bool) -> None:
        """Set or clear native topmost without touching window flags
        (no HWND re-creation -> the overlay can't get 'lost')."""
        try:
            import ctypes
            hwnd = int(self._window.winId())
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            # Never crash the overlay for a cosmetic always-on-top; keep a log
            # line so the failure is visible instead of silent.
            log.warning("native always-on-top %s failed",
                        "setup" if on else "clear")

    def _set_topmost_qt(self, on: bool) -> None:
        """Add/remove the Qt topmost hint (generic X11/Wayland only).

        Toggling this flag re-creates the native window, so a visible window
        is re-shown to stay on screen; nothing is done when the flag already
        matches, to avoid a needless re-creation.
        """
        have = bool(self._window.windowFlags()
                    & Qt.WindowType.WindowStaysOnTopHint)
        if have == on:
            return
        self._window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if self._window.isVisible():
            self._window.show()

    def _log_hyprland_stacking(self) -> None:
        """Hyprland owns stacking via compositor rules; say so in the log."""
        log.info("keep on top: Hyprland manages window stacking from its "
                 "compositor rules (requested=%s)", self._keep_on_top)

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
        # On Windows the native topmost assert must land *after* the window is
        # foreground. show() delivers showEvent synchronously, whose on_show
        # would otherwise assert topmost while the game still holds the
        # foreground, so suppress that one apply and take over once
        # activation has happened.
        self._engaging = sys.platform == "win32"
        try:
            self._window.show()
        finally:
            self._engaging = False
        self._window.raise_()
        self._window.activateWindow()
        self._window.setFocus()
        if sys.platform == "win32":
            self._settle_win32_topmost()
        # Qt only *asks*; on Hyprland a compositor rule can still keep the
        # palette behind the game, so nudge the compositor directly. No-op
        # (and quiet) on every other session.
        raisewindow.focus_window()

    def _settle_win32_topmost(self) -> None:
        """Assert topmost after activation, then re-assert a bounded burst.

        Called from :meth:`engage` only on Windows, after the window has been
        raised and activated. Windows drops the native topmost state when the
        game takes the foreground and the game re-asserts its own topmost as
        soon as it loses it, so a single assert can lose that race. Re-assert
        a fixed, small number of times over a couple of hundred milliseconds
        and then stop: an open-ended timer would fight the game forever and
        keep the overlay above windows the user brought forward on purpose.
        With keep-on-top off there is nothing to assert, so raising and
        activating alone are the whole job and no topmost call is made.
        """
        if not self._keep_on_top:
            log.info("engage: raised and activated (keep on top off)")
            return
        self._set_topmost_win32(True)
        self._topmost_reasserts_left = WIN32_TOPMOST_REASSERTS
        if self._topmost_reasserts_left > 0:
            if self._topmost_timer is None:
                self._topmost_timer = QTimer(self)
                self._topmost_timer.setInterval(
                    WIN32_TOPMOST_REASSERT_INTERVAL_MS)
                self._topmost_timer.timeout.connect(
                    self._reassert_win32_topmost)
            self._topmost_timer.start()
        log.info("engage: asserted topmost after activation, %d bounded "
                 "re-asserts over %d ms", WIN32_TOPMOST_REASSERTS,
                 WIN32_TOPMOST_REASSERTS * WIN32_TOPMOST_REASSERT_INTERVAL_MS)

    def _reassert_win32_topmost(self) -> None:
        """One bounded re-assert; stop the timer once the burst is spent."""
        if not self._keep_on_top or not self._window.isVisible():
            self._stop_topmost_timer()
            return
        self._set_topmost_win32(True)
        self._topmost_reasserts_left -= 1
        if self._topmost_reasserts_left <= 0:
            self._stop_topmost_timer()

    def _stop_topmost_timer(self) -> None:
        """Stop the bounded topmost burst, if one is running."""
        if self._topmost_timer is not None:
            self._topmost_timer.stop()

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
        """Apply the keep-on-top choice when the window is shown.

        Windows loses the native topmost state when the window is re-created,
        so it is re-asserted here; the generic X11/Wayland hint and the
        Hyprland log note are idempotent. The one exception is a Windows
        engage: :meth:`engage` suppresses this apply and asserts topmost
        itself once the window is foreground, which is what stops the
        topmost flicker. Linux and Hyprland are untouched by that guard.
        """
        if self._engaging:
            return
        self._apply_keep_on_top()

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
