"""Global hotkey support.

The overlay is summoned with a user-chosen global hotkey (Ctrl+Shift+B out
of the box) after clicking a cat in the game.

  * Windows - ``RegisterHotKey`` + a ``QAbstractNativeEventFilter``. Works
    with the game in any window mode. This is the primary target (most
    Mewgenics players are on Windows).
  * Linux - no portable global-grab API exists that works on both X11 and
    Wayland without swallowing keys, so ``install()`` returns an *inactive*
    hotkey and the app falls back to the focused-window shortcut plus the
    system-tray toggle. (Pluggable extension point: implement a native
    X11/pyinput backend here later.)

Two registration targets matter on Windows: ``RegisterHotKey(NULL, …)``
posts ``WM_HOTKEY`` to the *thread* queue (delivered to Qt as
``windows_dispatcher_MSG``) while a real window handle posts it to that
window (``windows_generic_MSG``). A hidden helper widget provides the handle,
and the filter accepts **both** event types, so neither delivery mode can
silently swallow the key.

The Win32 message is decoded with a fixed-width ``ctypes`` struct (not
``ctypes.wintypes``, which only exists on Windows), which keeps this module
importable and the filter testable everywhere.

Usage:

    hk = hotkey.install(app, callback, binding)
    hk.rebind(hotkeybinding.parse("Ctrl+Alt+K"))
    ...
    hk.uninstall()
"""

from __future__ import annotations

import ctypes
import logging
import operator
import sys
from typing import Callable, Optional

log = logging.getLogger("mewgenics_overlay.hotkey")

from PySide6.QtCore import Qt, QAbstractNativeEventFilter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

# The pointer type Qt actually hands the native filter. Imported defensively
# (and cached) so this module stays importable without shiboken bindings.
try:
    from shiboken6 import VoidPtr as _VOID_PTR  # noqa: E402
except ImportError:
    try:
        from shiboken2 import VoidPtr as _VOID_PTR  # noqa: E402
    except ImportError:
        _VOID_PTR = None

from mewgenics_overlay.ui.hotkeybinding import (  # noqa: E402
    DEFAULT_BINDING,
    HotkeyBinding,
)


_HOTKEY_ID = 0xBEEF
_WM_HOTKEY = 0x0312

# Qt's name for a Win32 window message and for a thread-queue message. Both
# can carry WM_HOTKEY depending on the handle RegisterHotKey was given.
_WINDOWS_EVENT_TYPES = (b"windows_generic_MSG", b"windows_dispatcher_MSG")


class MSG(ctypes.Structure):
    """The Win32 ``MSG`` struct in fixed-width types.

    ``WPARAM``/``LPARAM`` are pointer-sized and ``LONG`` is always 32-bit,
    which matters on 64-bit Windows (and keeps the layout identical when a
    test builds one on Linux: ``ctypes.wintypes`` does not exist there).
    """

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt_x", ctypes.c_int32),
        ("pt_y", ctypes.c_int32),
    ]


def _event_name(event_type) -> bytes:
    """Normalise the Qt event-type argument (a QByteArray) to bytes."""
    if isinstance(event_type, bytes):
        return event_type
    if isinstance(event_type, str):
        return event_type.encode("ascii", "ignore")
    try:
        return bytes(event_type)
    except (TypeError, ValueError):
        return b""


def _address_of(message) -> int:
    """Best-effort pointer for the native message, or 0 when undecodable.

    Only genuine pointer sources are trusted: an ``int`` (via
    :func:`operator.index`, which rejects ``float`` and, with the explicit
    bool check, ``True``/``False``), a ``ctypes`` pointer, or the
    ``Shiboken.VoidPtr`` Qt actually delivers. Anything else is a Python
    object whose numeric interpretation would be a small nonzero address, so
    :func:`_decode` would read unrelated memory as a Win32 ``MSG`` and fault.
    Coercing with ``int()`` first (which accepts ``1.5`` and ``True``) is
    exactly what this refuses to do.
    """
    if isinstance(message, bool) or message is None:
        return 0
    if isinstance(message, (str, bytes, bytearray, memoryview)):
        return 0
    if _VOID_PTR is not None and isinstance(message, _VOID_PTR):
        try:
            value = int(message)
        except (TypeError, ValueError, OverflowError):
            return 0
        return value if value else 0
    if isinstance(message, ctypes.c_void_p):
        return int(message.value or 0)
    try:
        index = operator.index(message)
    except TypeError:
        return 0
    return index if index else 0


def _decode(message) -> Optional[MSG]:
    """Decode the native message into a :class:`MSG`, or ``None``.

    The field reads happen *inside* the guard: touching ``.message`` /
    ``.wParam`` is where a bad address would be dereferenced, so the caller
    only ever sees an already-read message or ``None``.
    """
    if isinstance(message, MSG):
        return message
    address = _address_of(message)
    if not address:
        return None
    try:
        msg = MSG.from_address(address)
        int(msg.message)
        int(msg.wParam)
    except (TypeError, ValueError, OSError, ctypes.ArgumentError) as exc:
        log.debug("could not decode the native hotkey message: %s", exc)
        return None
    return msg


def _user32():
    """The Win32 user32 module, or ``None`` on a non-Windows host."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    try:
        return windll.user32
    except (AttributeError, OSError) as exc:
        log.warning("could not load user32: %s", exc)
        return None


def _declare_prototypes(user32) -> None:
    """Pin the argument/return types so the calls are 64-bit correct."""
    user32.RegisterHotKey.argtypes = [
        ctypes.c_void_p,    # HWND (NULL = thread queue, else the helper)
        ctypes.c_int,       # id
        ctypes.c_uint,      # fsModifiers
        ctypes.c_uint,      # vk
    ]
    user32.RegisterHotKey.restype = ctypes.c_int
    user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.UnregisterHotKey.restype = ctypes.c_int


def _win_error() -> str:
    """Readable text for the last Win32 error; never raises."""
    win_error = getattr(ctypes, "WinError", None)
    if win_error is None:
        return "unknown Win32 error"
    try:
        return str(win_error())
    except (ValueError, OSError) as exc:
        log.debug("could not read the Win32 error text: %s", exc)
        return "unknown Win32 error"


def _make_helper_widget() -> Optional[QWidget]:
    """A hidden top-level widget whose handle receives ``WM_HOTKEY``.

    Unlike a NULL handle (thread queue), a real window avoids relying on
    ``windows_dispatcher_MSG`` delivery, which Qt's filter interface does not
    always surface consistently.
    """
    try:
        widget = QWidget(None, Qt.WindowType.Tool)
        widget.setWindowTitle("Mewgenics overlay hotkey")
        return widget
    except Exception:
        log.exception("could not create the hotkey helper window")
        return None


def _native_handle(widget: Optional[QWidget]) -> Optional[int]:
    """Force native-window creation and return its handle (0 -> None)."""
    if widget is None:
        return None
    try:
        handle = int(widget.winId())
    except Exception:
        log.exception("could not create the native hotkey helper handle")
        return None
    return handle or None


class _WindowsHotkeyFilter(QAbstractNativeEventFilter):
    """RegisterHotKey + native event filter for one combination at a time."""

    def __init__(self, callback: Callable[[], None],
                 helper: Optional[QWidget] = None):
        super().__init__()
        self._cb = callback
        self._registered = False
        self._hwnd = None
        self._helper = helper

    @property
    def registered(self) -> bool:
        return self._registered

    def register(self, binding: HotkeyBinding,
                 hwnd: Optional[int] = None) -> tuple[bool, str]:
        """Try to grab *binding*; returns ``(ok, readable error)``."""
        user32 = _user32()
        if user32 is None:
            return False, "the Windows user32 API is unavailable"
        _declare_prototypes(user32)
        hwnd_arg = ctypes.c_void_p(hwnd) if hwnd else None
        ok = user32.RegisterHotKey(hwnd_arg, _HOTKEY_ID, binding.mods(),
                                   binding.vk())
        if ok:
            self._registered = True
            self._hwnd = hwnd or None
            log.info("global hotkey registered: %s", binding.format())
            return True, ""
        error = _win_error()
        log.warning("could not register global hotkey %s: %s",
                    binding.format(), error)
        return False, error

    def _current_hwnd(self) -> Optional[int]:
        """Re-derive the helper handle instead of trusting the cached one,
        so a recreated window cannot leave a grab behind on its old handle."""
        if self._helper is not None:
            return _native_handle(self._helper)
        return self._hwnd

    def unregister(self) -> None:
        """Release the current grab (no-op when nothing is registered)."""
        if not self._registered:
            return
        target = self._current_hwnd()
        user32 = _user32()
        if user32 is not None:
            _declare_prototypes(user32)
            hwnd_arg = ctypes.c_void_p(target) if target else None
            if not user32.UnregisterHotKey(hwnd_arg, _HOTKEY_ID):
                log.warning("could not unregister global hotkey: %s",
                            _win_error())
        self._registered = False
        self._hwnd = None

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt API)
        """PySide6 passes the Win32 MSG as a Shiboken.VoidPtr; decode it with
        ctypes so a hotkey never crashes the event loop."""
        if not self._registered:
            return False, 0
        if _event_name(event_type) not in _WINDOWS_EVENT_TYPES:
            return False, 0
        msg = _decode(message)
        if msg is None:
            return False, 0
        if msg.message == _WM_HOTKEY and int(msg.wParam) == _HOTKEY_ID:
            log.debug("global hotkey fired")
            try:
                self._cb()
            except Exception:
                log.exception("global hotkey callback failed")
            return True, 0
        return False, 0


class Hotkey:
    """Platform hotkey handle; ``active`` is False when nothing is grabbed.

    On Windows the handle is always present (even right after a failed grab)
    so ``rebind`` can retry a free combination later; ``active`` then tells
    the app whether the global grab currently works, and the focused-window
    shortcut/tray remain the fallbacks.
    """

    def __init__(self, impl: Optional[_WindowsHotkeyFilter], app=None,
                 binding: HotkeyBinding = DEFAULT_BINDING,
                 helper: Optional[QWidget] = None):
        self._impl = impl
        self._app = app
        self._binding = binding
        self._helper = helper

    @property
    def binding(self) -> HotkeyBinding:
        return self._binding

    @property
    def description(self) -> str:
        return self._binding.format()

    @property
    def active(self) -> bool:
        return self._impl is not None and self._impl.registered

    def rebind(self, binding: HotkeyBinding) -> tuple[bool, str]:
        """Switch to *binding*, restoring the previous combo on failure.

        Returns ``(ok, readable error)``; ``ok`` is True on platforms with no
        global grab (the caller keeps the focused-window/tray fallback) and
        False only when the new combo could not be registered and the old one
        is (best-effort) kept, so the app is never left without a hotkey.
        """
        if self._impl is None:
            self._binding = binding
            log.info("no global hotkey on this platform; the combo %s drives "
                     "the focused-window shortcut", binding.format())
            return True, ""
        previous = self._binding
        self._impl.unregister()
        ok, error = self._impl.register(binding, self._hwnd())
        if ok:
            self._binding = binding
            return True, ""
        restored, restore_error = self._impl.register(
            previous, self._hwnd())
        self._binding = previous
        if restored:
            log.warning("global hotkey %s unavailable; kept %s",
                        binding.format(), previous.format())
        else:
            log.warning("global hotkey %s unavailable (%s) and %s could not be "
                        "restored (%s); use the tray icon",
                        binding.format(), error, previous.format(),
                        restore_error)
        return False, error

    def _hwnd(self) -> Optional[int]:
        """The helper handle, re-derived in case the native window was
        recreated (it is kept on this object so it is never collected)."""
        if self._helper is None:
            return None
        return _native_handle(self._helper)

    def uninstall(self) -> None:
        """Release the grab and drop the helper window."""
        if self._impl is not None:
            if self._app is not None:
                self._app.removeNativeEventFilter(self._impl)
            self._impl.unregister()
            self._impl = None
        if self._helper is not None:
            self._helper.deleteLater()
            self._helper = None


def install(app, callback: Callable[[], None],
            binding: HotkeyBinding = DEFAULT_BINDING) -> Hotkey:
    """Register the *binding* toggle for *app* (a QApplication).

    On non-Windows hosts this returns an inactive :class:`Hotkey`: the app
    keeps its focused-window shortcut and tray toggle.
    """
    bind = binding or DEFAULT_BINDING
    helper = None
    impl = None
    if sys.platform == "win32":
        helper = _make_helper_widget()
        impl = _WindowsHotkeyFilter(callback, helper)
        # ``register`` logs INFO on success and WARNING with the Win32 error
        # on failure; the app logs the tray/focused-window fallback.
        impl.register(bind, _native_handle(helper))
        if app is not None:
            app.installNativeEventFilter(impl)
    else:
        log.info("no global hotkey support on %s; the focused-window shortcut "
                 "and the tray icon toggle the overlay", sys.platform)
    return Hotkey(impl, app=app, binding=bind, helper=helper)
