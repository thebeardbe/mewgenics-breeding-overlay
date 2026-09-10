"""``ui/hotkey.py``: Win32 filter decoding, rebinding and platform install.

The real ``RegisterHotKey``/``GetLastError`` calls only exist on Windows, so
the OS itself is out of scope here (see the gap note in the report). What is
covered is the code that runs on every platform: the event-type/pointer
decoding, the ``wParam``/message guard, the callback exception guard, the
``Hotkey`` rebind state machine (restoring the previous combo) and the
non-Windows ``install`` fallback.

The Windows registration wrapper is exercised against a bare stand-in object
exposed at the module seam (``_user32``) purely to pin the ``(ok, error)``
mapping and the ``registered`` flag; it is not a substitute for testing the
real syscall.
"""

from __future__ import annotations

import ctypes
import os
import sys

# Must be set before the first QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from mewgenics_overlay.ui import hotkey as hk  # noqa: E402
from mewgenics_overlay.ui.hotkeybinding import (  # noqa: E402
    DEFAULT_BINDING,
    HotkeyBinding,
)


# ── helpers / fakes ────────────────────────────────────────────────────────
class FakeImpl:
    """Stand-in for ``_WindowsHotkeyFilter`` in ``Hotkey`` rebind tests."""

    def __init__(self, results=None):
        self.registered = False
        self.calls = []
        # Each register() pops the next (ok, error); exhausted -> success.
        self._results = list(results or [])

    def register(self, binding, hwnd=None):
        self.calls.append(("register", binding.format(), hwnd))
        ok, error = ((True, "") if not self._results
                     else self._results.pop(0))
        self.registered = bool(ok)
        return ok, error

    def unregister(self):
        self.calls.append(("unregister",))
        self.registered = False


class FakeApp:
    def __init__(self):
        self.installed = []
        self.removed = []

    def installNativeEventFilter(self, f):
        self.installed.append(f)

    def removeNativeEventFilter(self, f):
        self.removed.append(f)


def _msg(message=hk._WM_HOTKEY, wparam=hk._HOTKEY_ID):
    m = hk.MSG()
    m.message = message
    m.wParam = wparam
    m.lParam = 0
    return m


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ── 1. event-name normalisation ────────────────────────────────────────────
def test_event_name_accepts_bytes_and_str():
    assert hk._event_name(b"windows_generic_MSG") == b"windows_generic_MSG"
    assert hk._event_name("windows_dispatcher_MSG") == b"windows_dispatcher_MSG"


def test_event_name_returns_empty_bytes_for_undecodable_input():
    assert hk._event_name(None) == b""
    assert hk._event_name(object()) == b""


# ── 2. pointer decoding ────────────────────────────────────────────────────
def test_address_of_accepts_ints_and_void_pointers():
    assert hk._address_of(0x1234) == 0x1234
    assert hk._address_of(ctypes.c_void_p(0x99)) == 0x99


@pytest.mark.skipif(hk._VOID_PTR is None,
                    reason="shiboken VoidPtr bindings are unavailable")
def test_address_of_decodes_the_shiboken_void_ptr_qt_delivers():
    assert hk._address_of(hk._VOID_PTR(0)) == 0
    assert hk._address_of(hk._VOID_PTR(0x1234)) == 0x1234


@pytest.mark.parametrize("bad", [
    None, object(), "not-a-pointer",
    # Non-integral sources must never be coerced: int(1.5) == 1,
    # int(True) == 1 and a buffer's length would all read a bogus small
    # address as a Win32 MSG.
    1.5, 0.0, 1.0, 2.0, True, False,
    bytearray(b"\x01\x02"), memoryview(b"\x00\x01"), b"\x01\x02",
])
def test_address_of_returns_zero_for_non_integral_and_undecodable_input(bad):
    assert hk._address_of(bad) == 0


def test_decode_passes_a_msg_instance_through():
    msg = _msg()
    assert hk._decode(msg) is msg


def test_decode_reads_a_msg_from_a_live_buffer():
    buf = _msg(message=hk._WM_HOTKEY, wparam=hk._HOTKEY_ID)
    address = ctypes.addressof(buf)

    decoded = hk._decode(address)

    assert decoded is not None
    assert decoded.message == hk._WM_HOTKEY
    assert int(decoded.wParam) == hk._HOTKEY_ID


@pytest.mark.parametrize("bad", [
    0, None, object(),
    1.5, 0.0, 2.0, True, False,
    bytearray(b"\x01"), memoryview(b"\x00\x01"), b"\x01",
])
def test_decode_returns_none_without_a_pointer(bad):
    assert hk._decode(bad) is None


def test_decode_returns_none_when_addressing_fails(monkeypatch):
    """A bad pointer must not escape as an exception (event-loop crash)."""

    class ExplodingAddress:
        @classmethod
        def from_address(cls, address):
            raise OSError("bad address")

    monkeypatch.setattr(hk, "MSG", ExplodingAddress)

    assert hk._decode(0xDEAD) is None


def test_decode_reads_the_message_fields_inside_the_guard(monkeypatch, caplog):
    """Reading ``.message``/``.wParam`` is itself the dereference, so the
    field access must sit inside the try: a faulting read returns None."""

    class ExplodingMsg:
        @property
        def message(self):
            raise ValueError("read past the end of the buffer")

        wParam = 0

    class FakeMSG:
        @classmethod
        def from_address(cls, address):
            return ExplodingMsg()

    monkeypatch.setattr(hk, "MSG", FakeMSG)

    with caplog.at_level("DEBUG", logger="mewgenics_overlay.hotkey"):
        assert hk._decode(0xDEAD) is None

    assert any("could not decode the native hotkey message" in r.message
               for r in caplog.records)


def test_decode_returns_none_when_a_field_read_raises_argument_error(
        monkeypatch):
    """ctypes raises ArgumentError for a malformed read; it is guarded too."""

    class ExplodingMsg:
        message = hk._WM_HOTKEY

        @property
        def wParam(self):
            raise ctypes.ArgumentError("bad field")

    class FakeMSG:
        @classmethod
        def from_address(cls, address):
            return ExplodingMsg()

    monkeypatch.setattr(hk, "MSG", FakeMSG)

    assert hk._decode(0xDEAD) is None


# ── 3. the native event filter ─────────────────────────────────────────────
def _registered_filter(callback):
    filt = hk._WindowsHotkeyFilter(callback)
    filt._registered = True
    return filt


@pytest.mark.parametrize("event_type", [
    b"windows_generic_MSG",
    b"windows_dispatcher_MSG",
])
def test_filter_accepts_both_windows_event_types(event_type):
    fired = []
    filt = _registered_filter(lambda: fired.append(True))

    handled, result = filt.nativeEventFilter(event_type, _msg())

    assert handled is True
    assert result == 0
    assert fired == [True]


def test_filter_decodes_a_real_pointer_argument():
    fired = []
    filt = _registered_filter(lambda: fired.append(True))
    buf = _msg()
    pointer = ctypes.cast(ctypes.pointer(buf), ctypes.c_void_p)

    handled, _ = filt.nativeEventFilter(b"windows_generic_MSG", pointer)

    assert handled is True
    assert fired == [True]


@pytest.mark.parametrize("event_type", [
    b"windows_generic_MSG",
    b"windows_dispatcher_MSG",
])
def test_filter_decodes_a_live_buffer_for_both_event_types(event_type):
    """Both delivery modes decode a real MSG and fire exactly once."""
    fired = []
    filt = _registered_filter(lambda: fired.append(True))
    buf = _msg()

    handled, result = filt.nativeEventFilter(event_type,
                                             ctypes.addressof(buf))

    assert handled is True
    assert result == 0
    assert fired == [True]


def test_filter_fires_exactly_once_for_a_plain_integer_address():
    """A bare int address is a genuine pointer source and must not be
    rejected, nor must it fire twice for a single event."""
    fired = []
    filt = _registered_filter(lambda: fired.append(True))
    buf = _msg(message=hk._WM_HOTKEY, wparam=hk._HOTKEY_ID)

    handled, _ = filt.nativeEventFilter(b"windows_generic_MSG",
                                        ctypes.addressof(buf))

    assert handled is True
    assert fired == [True]


@pytest.mark.skipif(hk._VOID_PTR is None,
                    reason="shiboken VoidPtr bindings are unavailable")
def test_filter_fires_for_a_shiboken_void_ptr_message():
    """The exact pointer type Qt hands the native filter still decodes and
    fires once (the OS delivery itself is a Windows-only gap)."""
    fired = []
    filt = _registered_filter(lambda: fired.append(True))
    buf = _msg()
    pointer = hk._VOID_PTR(ctypes.addressof(buf))

    handled, result = filt.nativeEventFilter(b"windows_generic_MSG", pointer)

    assert handled is True
    assert result == 0
    assert fired == [True]


@pytest.mark.parametrize("bad", [1.5, True, False, None,
                                 bytearray(b"\x01\x02"),
                                 memoryview(b"\x00\x01")])
def test_filter_never_fires_on_a_bogus_pointer(bad):
    """Non-integral pointers must be dropped, not dereferenced."""
    fired = []
    filt = _registered_filter(lambda: fired.append(True))

    handled, result = filt.nativeEventFilter(b"windows_generic_MSG", bad)

    assert handled is False
    assert result == 0
    assert fired == []


@pytest.mark.parametrize("event_type", [
    b"windows_key_event",
    b"windows_mouse_event",
    b"",
    None,
    "not-a-windows-event",
])
def test_filter_rejects_other_event_types(event_type):
    fired = []
    filt = _registered_filter(lambda: fired.append(True))

    handled, result = filt.nativeEventFilter(event_type, _msg())

    assert handled is False
    assert result == 0
    assert fired == []


@pytest.mark.parametrize("message", [0x0100, 0x0000, hk._WM_HOTKEY + 1])
def test_filter_rejects_other_window_messages(message):
    fired = []
    filt = _registered_filter(lambda: fired.append(True))

    handled, _ = filt.nativeEventFilter(b"windows_generic_MSG",
                                        _msg(message=message))

    assert handled is False
    assert fired == []


@pytest.mark.parametrize("wparam", [0, 1, hk._HOTKEY_ID + 1, hk._HOTKEY_ID * 2])
def test_filter_rejects_other_hotkey_ids(wparam):
    fired = []
    filt = _registered_filter(lambda: fired.append(True))

    handled, _ = filt.nativeEventFilter(b"windows_generic_MSG",
                                        _msg(wparam=wparam))

    assert handled is False
    assert fired == []


def test_filter_ignores_everything_before_a_successful_register():
    fired = []
    filt = hk._WindowsHotkeyFilter(lambda: fired.append(True))
    assert filt.registered is False

    handled, _ = filt.nativeEventFilter(b"windows_generic_MSG", _msg())

    assert handled is False
    assert fired == []


def test_filter_ignores_undecodable_messages():
    fired = []
    filt = _registered_filter(lambda: fired.append(True))

    for bad in (0, None, object()):
        handled, _ = filt.nativeEventFilter(b"windows_generic_MSG", bad)
        assert handled is False
    assert fired == []


def test_callback_exception_does_not_escape_the_filter(caplog):
    def boom():
        raise RuntimeError("callback exploded")

    filt = _registered_filter(boom)

    with caplog.at_level("ERROR", logger="mewgenics_overlay.hotkey"):
        handled, result = filt.nativeEventFilter(b"windows_generic_MSG",
                                                 _msg())

    assert handled is True          # the event was ours; it was consumed
    assert result == 0
    assert any("global hotkey callback failed" in r.message
               for r in caplog.records)


def test_a_fire_is_logged_at_debug(caplog):
    filt = _registered_filter(lambda: None)

    with caplog.at_level("DEBUG", logger="mewgenics_overlay.hotkey"):
        filt.nativeEventFilter(b"windows_generic_MSG", _msg())

    assert any("global hotkey fired" in r.message for r in caplog.records)


# ── 4. the Win32 registration wrapper (stand-in user32) ─────────────────────
def test_declare_prototypes_pins_pointer_sized_and_int_types():
    class FakeUser32:
        RegisterHotKey = lambda *a: 1          # noqa: E731
        UnregisterHotKey = lambda *a: 1        # noqa: E731

    hk._declare_prototypes(FakeUser32)

    assert FakeUser32.RegisterHotKey.argtypes == [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    assert FakeUser32.RegisterHotKey.restype is ctypes.c_int
    assert FakeUser32.UnregisterHotKey.argtypes == [
        ctypes.c_void_p, ctypes.c_int]
    assert FakeUser32.UnregisterHotKey.restype is ctypes.c_int


def _install_fake_user32(monkeypatch, ok):
    calls = []

    class FakeUser32:
        def RegisterHotKey(hwnd, hk_id, mods, vk):
            calls.append(("register", hwnd, hk_id, mods, vk))
            return 1 if ok else 0

        def UnregisterHotKey(hwnd, hk_id):
            calls.append(("unregister", hwnd, hk_id))
            return 1

    monkeypatch.setattr(hk, "_user32", lambda: FakeUser32)
    return calls


def test_register_success_sets_the_flag_and_passes_the_combo(monkeypatch):
    calls = _install_fake_user32(monkeypatch, ok=True)
    filt = hk._WindowsHotkeyFilter(lambda: None)

    ok, error = filt.register(DEFAULT_BINDING, hwnd=0x4321)

    assert (ok, error) == (True, "")
    assert filt.registered is True
    kind, hwnd, hotkey_id, mods, vk = calls[0]
    assert kind == "register"
    assert int(hwnd.value) == 0x4321
    assert hotkey_id == hk._HOTKEY_ID
    assert mods == DEFAULT_BINDING.mods()
    assert vk == DEFAULT_BINDING.vk()
    assert hk._WM_HOTKEY == 0x0312


def test_register_failure_returns_the_win_error_text(monkeypatch):
    _install_fake_user32(monkeypatch, ok=False)
    monkeypatch.setattr(hk, "_win_error",
                        lambda: "the hotkey is already registered")
    filt = hk._WindowsHotkeyFilter(lambda: None)

    ok, error = filt.register(DEFAULT_BINDING)

    assert ok is False
    assert error == "the hotkey is already registered"
    assert filt.registered is False


def test_register_without_a_user32_module_is_a_readable_failure(monkeypatch):
    monkeypatch.setattr(hk, "_user32", lambda: None)
    filt = hk._WindowsHotkeyFilter(lambda: None)

    ok, error = filt.register(DEFAULT_BINDING)

    assert ok is False
    assert error


def test_unregister_falls_back_to_the_cache_without_a_helper(monkeypatch):
    calls = _install_fake_user32(monkeypatch, ok=True)
    filt = hk._WindowsHotkeyFilter(lambda: None)
    filt._registered = True
    filt._hwnd = 0x1234

    filt.unregister()

    kind, hwnd, hotkey_id = calls[-1]
    assert kind == "unregister"
    assert int(hwnd.value) == 0x1234
    assert hotkey_id == hk._HOTKEY_ID


def test_unregister_is_a_noop_when_not_registered(monkeypatch):
    calls = _install_fake_user32(monkeypatch, ok=True)
    filt = hk._WindowsHotkeyFilter(lambda: None)

    filt.unregister()

    assert calls == []
    assert filt.registered is False


def test_unregister_releases_the_grab(monkeypatch):
    calls = _install_fake_user32(monkeypatch, ok=True)
    filt = hk._WindowsHotkeyFilter(lambda: None)
    filt.register(DEFAULT_BINDING, hwnd=0x1)

    filt.unregister()

    kind, hwnd, hotkey_id = calls[-1]
    assert kind == "unregister"
    assert int(hwnd.value) == 0x1
    assert hotkey_id == hk._HOTKEY_ID
    assert filt.registered is False


class _Helper:
    """Stand-in helper window whose native handle can change."""

    def __init__(self, handle):
        self._handle = handle

    def winId(self):
        return self._handle


def test_current_hwnd_prefers_a_live_helper_over_the_cache():
    filt = hk._WindowsHotkeyFilter(lambda: None,
                                   helper=_Helper(0x2222))
    filt._hwnd = 0xAAAA          # stale cache

    assert filt._current_hwnd() == 0x2222


def test_unregister_re_derives_a_recreated_helper_handle(monkeypatch):
    """A recreated helper window must be released on its *new* handle, not
    the cached one, or the grab would leak on the old window."""
    calls = _install_fake_user32(monkeypatch, ok=True)
    # The window was recreated after registration: the helper now reports a
    # different handle than the one stored in ``_hwnd``.
    filt = hk._WindowsHotkeyFilter(lambda: None,
                                   helper=_Helper(0x2222))
    filt._registered = True
    filt._hwnd = 0xAAAA

    filt.unregister()

    kind, hwnd, hotkey_id = calls[-1]
    assert kind == "unregister"
    assert int(hwnd.value) == 0x2222
    assert hotkey_id == hk._HOTKEY_ID
    assert filt.registered is False


def test_win_error_never_raises():
    assert isinstance(hk._win_error(), str)
    assert hk._win_error() != ""


# ── 5. helper widget / handle ──────────────────────────────────────────────
def test_make_helper_widget_is_a_titled_tool_window(qapp):
    widget = hk._make_helper_widget()

    assert isinstance(widget, QWidget)
    assert widget.windowTitle() == "Mewgenics overlay hotkey"
    widget.deleteLater()


def test_native_handle_none_for_missing_widget(qapp):
    assert hk._native_handle(None) is None


def test_native_handle_creates_and_returns_a_real_handle(qapp):
    widget = hk._make_helper_widget()
    handle = hk._native_handle(widget)

    assert isinstance(handle, int) and handle > 0
    widget.deleteLater()


# ── 6. Hotkey: properties, rebind state machine, uninstall ─────────────────
def test_hotkey_description_and_binding_reflect_the_combo():
    binding = HotkeyBinding(True, False, False, "Q")
    hk_obj = hk.Hotkey(None, binding=binding)

    assert hk_obj.binding == binding
    assert hk_obj.description == "Ctrl+Q"
    assert hk_obj.active is False


def test_rebind_without_a_platform_impl_updates_the_combo():
    hk_obj = hk.Hotkey(None)
    new = HotkeyBinding(True, True, False, "K")

    ok, error = hk_obj.rebind(new)

    assert (ok, error) == (True, "")
    assert hk_obj.binding == new
    assert hk_obj.description == "Ctrl+Alt+K"
    assert hk_obj.active is False


def test_rebind_success_swaps_the_grab_and_updates_the_combo():
    impl = FakeImpl()
    impl.registered = True
    hk_obj = hk.Hotkey(impl, binding=DEFAULT_BINDING)
    new = HotkeyBinding(True, True, False, "K")

    ok, error = hk_obj.rebind(new)

    assert (ok, error) == (True, "")
    assert hk_obj.binding == new
    assert hk_obj.active is True
    assert impl.calls == [("unregister",), ("register", "Ctrl+Alt+K", None)]


def test_rebind_failure_keeps_and_restores_the_previous_combo():
    # new combo fails, the previous one is still free and must be re-grabbed.
    impl = FakeImpl(results=[(False, "taken"), (True, "")])
    impl.registered = True
    previous = DEFAULT_BINDING
    hk_obj = hk.Hotkey(impl, binding=previous)

    ok, error = hk_obj.rebind(HotkeyBinding(True, True, False, "K"))

    assert ok is False
    assert error == "taken"
    assert hk_obj.binding == previous
    assert hk_obj.active is True
    assert impl.calls == [
        ("unregister",),
        ("register", "Ctrl+Alt+K", None),
        ("register", "Ctrl+Shift+B", None),
    ]


def test_rebind_failure_when_restore_also_fails_leaves_the_old_combo():
    impl = FakeImpl(results=[(False, "taken"), (False, "also taken")])
    impl.registered = True
    previous = DEFAULT_BINDING
    hk_obj = hk.Hotkey(impl, binding=previous)

    ok, error = hk_obj.rebind(HotkeyBinding(True, True, False, "K"))

    assert ok is False
    assert error == "taken"          # the user hears about the requested combo
    assert hk_obj.binding == previous
    assert hk_obj.active is False


def test_uninstall_releases_the_filter_and_the_helper(qapp):
    impl = FakeImpl()
    impl.registered = True
    app = FakeApp()
    helper = QWidget()
    hk_obj = hk.Hotkey(impl, app=app, helper=helper)

    hk_obj.uninstall()

    assert app.removed == [impl]
    assert impl.registered is False
    assert hk_obj._impl is None
    assert hk_obj._helper is None


def test_uninstall_without_an_app_still_releases_the_impl():
    impl = FakeImpl()
    impl.registered = True
    hk_obj = hk.Hotkey(impl)

    hk_obj.uninstall()

    assert impl.registered is False
    assert hk_obj._impl is None


def test_uninstall_twice_is_safe():
    hk_obj = hk.Hotkey(FakeImpl())

    hk_obj.uninstall()
    hk_obj.uninstall()          # must not raise

    assert hk_obj._impl is None


# ── 7. install() on a non-Windows host ─────────────────────────────────────
@pytest.fixture
def _linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(hk.sys, "platform", "linux")


def test_install_without_windows_returns_an_inactive_hotkey(_linux):
    app = FakeApp()

    hk_obj = hk.install(app, lambda: None, binding=DEFAULT_BINDING)

    assert hk_obj.active is False
    assert hk_obj.binding == DEFAULT_BINDING
    assert hk_obj.description == DEFAULT_BINDING.format()
    assert app.installed == []


def test_install_without_windows_still_honours_the_binding(_linux):
    binding = HotkeyBinding(True, True, True, "Z")

    hk_obj = hk.install(None, lambda: None, binding=binding)

    assert hk_obj.binding == binding
    assert hk_obj.active is False


def test_install_with_a_missing_binding_falls_back_to_the_default(_linux):
    hk_obj = hk.install(None, lambda: None, binding=None)

    assert hk_obj.binding is DEFAULT_BINDING
