"""Global hotkey support.

The overlay is summoned with a global hotkey (Ctrl+Shift+B) after clicking a
cat in the game.

  * Windows - RegisterHotKey + a QAbstractNativeEventFilter. Works with the
    game in any window mode. This is the primary target (most Mewgenics
    players are on Windows).
  * Linux - no portable global-grab API exists that works on both X11 and
    Wayland without swallowing keys, so `install()` returns an *inactive*
    hotkey and the app falls back to a system-tray toggle. (Pluggable
    extension point: implement a native X11/pyinput backend here later.)

Usage:

    hk = hotkey.install(app, callback)
    ...
    hk.uninstall()
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Optional

log = logging.getLogger("mewgenics_overlay.hotkey")

from PySide6.QtCore import QAbstractNativeEventFilter

import ctypes  # noqa: E402  (used inside the Windows filter)


_HOTKEY_ID = 0xBEEF


class _WindowsHotkeyFilter(QAbstractNativeEventFilter):
    MOD_CONTROL, MOD_SHIFT = 0x0002, 0x0004
    WM_HOTKEY = 0x0312
    MOD_NOREPEAT = 0x4000

    def __init__(self, callback: Callable[[], None]):
        super().__init__()
        self._cb = callback
        self._registered = False

    def register(self) -> bool:
        import ctypes
        user32 = ctypes.windll.user32
        mods = self.MOD_CONTROL | self.MOD_SHIFT | self.MOD_NOREPEAT
        self._registered = bool(user32.RegisterHotKey(None, _HOTKEY_ID, mods, ord("B")))
        if not self._registered:
            log.warning(
                "RegisterHotKey failed (error %s) - hotkey inactive; use the "
                "tray icon to summon the overlay",
                ctypes.windll.kernel32.GetLastError())
        return self._registered

    def unregister(self) -> None:
        import ctypes
        if self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, _HOTKEY_ID)
            self._registered = False

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt API)
        """PySide6 passes the Win32 MSG as a Shiboken.VoidPtr; decode it with
        ctypes so hotkeys never crash the event loop."""
        if not self._registered or event_type != b"windows_generic_MSG":
            return False, 0
        try:
            # Shiboken.VoidPtr -> memory address of the MSG struct
            address = int(message)
            msg = ctypes.wintypes.MSG.from_address(address)
        except (TypeError, ValueError, AttributeError):
            return False, 0
        if msg.message == self.WM_HOTKEY and msg.wParam == _HOTKEY_ID:
            try:
                self._cb()
            except Exception:
                pass
            return True, 0
        return False, 0


class Hotkey:
    """Platform hotkey handle; `active` is False when unsupported."""

    def __init__(self, impl: Optional[_WindowsHotkeyFilter], app=None):
        self._impl = impl
        self._app = app

    @property
    def active(self) -> bool:
        return self._impl is not None

    def uninstall(self) -> None:
        if self._impl is not None:
            if self._app is not None:
                self._app.removeNativeEventFilter(self._impl)
            self._impl.unregister()
            self._impl = None


def install(app, callback: Callable[[], None]) -> Hotkey:
    """Register the Ctrl+Shift+B toggle for *app* (a QApplication)."""
    if sys.platform == "win32":
        impl = _WindowsHotkeyFilter(callback)
        if impl.register():
            app.installNativeEventFilter(impl)
            return Hotkey(impl, app=app)
    return Hotkey(None)
